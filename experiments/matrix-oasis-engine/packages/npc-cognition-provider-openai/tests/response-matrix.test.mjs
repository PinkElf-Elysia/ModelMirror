import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import {
  NPC_COGNITION_CANONICALIZATION,
  NPC_COGNITION_CALL_PLAN_FORMAT,
  NPC_COGNITION_ENDPOINT,
  NPC_COGNITION_FORMAT_VERSION,
  NPC_COGNITION_LIMITS,
  NPC_COGNITION_MODEL,
  NPC_COGNITION_RETENTION_POLICY_VERSION,
  NPC_COGNITION_TRUSTED_INSTRUCTIONS,
  computeNpcCognitionApprovalHash,
} from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createOpenAiNpcCognitionProvider,
  executeApprovedNpcCognitionTurn,
} from "../src/index.mjs";

const sha256 = (text) => `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
const fixedSha = (character) => `sha256:${character.repeat(64)}`;
const choiceId = `choice-${"a".repeat(64)}`;

const CHECK_RULES = Object.freeze([
  "echo_frequency_penalty",
  "echo_presence_penalty",
  "echo_tool_usage",
  "echo_background",
  "echo_store",
  "echo_truncation",
  "echo_max_output_tokens",
  "echo_instructions",
  "echo_previous_response_id",
  "echo_conversation",
  "echo_prompt",
  "echo_tools",
  "echo_tool_choice",
  "echo_parallel_tool_calls",
  "echo_metadata",
  "echo_service_tier",
  "echo_text_format",
  "echo_schema",
  "usage",
  "model",
  "completion",
  "output",
  "message",
  "content",
  "proposal_json",
  "proposal_context",
  "dialogue_text",
  "action_choice",
]);
const CHECK_STATES = new Set(["passed", "failed", "not_checked"]);
const secretBait = "credential-bait-do-not-copy";
const responseIdBait = "response-bait-do-not-copy";
const messageIdBait = "message-bait-do-not-copy";

function signedFixture({ candidateChoices = [{ choiceId, intentSha256: fixedSha("a") }] } = {}) {
  const providerInput = canonicalizeJsonValue({ state: "ready", text: "synthetic" });
  const contextSha256 = sha256(providerInput);
  const responseSchema = {
    type: "object",
    additionalProperties: false,
    required: ["contextSha256", "dialogueText", "actionChoiceId"],
    properties: {
      contextSha256: { type: "string", const: contextSha256 },
      dialogueText: { type: "string", minLength: 1, maxLength: NPC_COGNITION_LIMITS.dialogueBytes },
      actionChoiceId: { type: ["string", "null"], enum: [null, ...candidateChoices.map((item) => item.choiceId)] },
    },
  };
  const request = {
    background: false,
    input: providerInput,
    instructions: NPC_COGNITION_TRUSTED_INSTRUCTIONS,
    max_output_tokens: NPC_COGNITION_LIMITS.maxOutputTokens,
    model: NPC_COGNITION_MODEL,
    reasoning: { effort: "none" },
    service_tier: "default",
    store: false,
    stream: false,
    text: { format: {
      name: "matrix_oasis_npc_dialogue_proposal",
      schema: responseSchema,
      strict: true,
      type: "json_schema",
    } },
    truncation: "disabled",
  };
  const providerRequestJson = canonicalizeJsonValue(request);
  const callPlan = {
    format: NPC_COGNITION_CALL_PLAN_FORMAT,
    formatVersion: NPC_COGNITION_FORMAT_VERSION,
    canonicalization: NPC_COGNITION_CANONICALIZATION,
    turnId: "turn-response-matrix",
    turnSha256: fixedSha("2"),
    contextSha256,
    candidateSha256: sha256(canonicalizeJsonValue(candidateChoices)),
    candidateChoices,
    providerPayloadSha256: sha256(providerRequestJson),
    responseSchemaSha256: sha256(canonicalizeJsonValue(responseSchema)),
    endpoint: NPC_COGNITION_ENDPOINT,
    model: NPC_COGNITION_MODEL,
    reasoningEffort: "none",
    priceLock: {
      inputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.inputMicrousdPerMillionTokens,
      cachedInputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.cachedInputMicrousdPerMillionTokens,
      cacheWriteInputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.cacheWriteInputMicrousdPerMillionTokens,
      outputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.outputMicrousdPerMillionTokens,
    },
    maxOutputTokens: NPC_COGNITION_LIMITS.maxOutputTokens,
    timeoutMs: NPC_COGNITION_LIMITS.timeoutMs,
    maxCostMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
    requestBytes: new TextEncoder().encode(providerRequestJson).byteLength,
    requestLimit: 1,
    retryLimit: 0,
    retentionPolicyVersion: NPC_COGNITION_RETENTION_POLICY_VERSION,
    retention: {
      store: false,
      zeroDataRetentionClaimed: false,
      abuseMonitoringMaxDays: 30,
      promptCachingPossible: true,
    },
    approval: { hash: fixedSha("0"), expiresAfterMs: NPC_COGNITION_LIMITS.approvalLifetimeMs },
  };
  callPlan.approval.hash = computeNpcCognitionApprovalHash(callPlan);
  return {
    callPlanJson: canonicalizeJsonValue(callPlan),
    providerRequestJson,
    approvalHash: callPlan.approval.hash,
  };
}

function resign(input, mutateRequest) {
  const request = JSON.parse(input.providerRequestJson);
  const callPlan = JSON.parse(input.callPlanJson);
  mutateRequest(request);
  const providerRequestJson = canonicalizeJsonValue(request);
  callPlan.providerPayloadSha256 = sha256(providerRequestJson);
  callPlan.responseSchemaSha256 = sha256(canonicalizeJsonValue(request.text.format.schema));
  callPlan.requestBytes = new TextEncoder().encode(providerRequestJson).byteLength;
  callPlan.approval.hash = fixedSha("0");
  callPlan.approval.hash = computeNpcCognitionApprovalHash(callPlan);
  return { callPlanJson: canonicalizeJsonValue(callPlan), providerRequestJson, approvalHash: callPlan.approval.hash };
}

function validEnvelope(input) {
  const request = JSON.parse(input.providerRequestJson);
  const contextSha256 = JSON.parse(input.callPlanJson).contextSha256;
  return {
    id: responseIdBait,
    object: "response",
    status: "completed",
    error: null,
    incomplete_details: null,
    model: NPC_COGNITION_MODEL,
    output: [{
      id: messageIdBait,
      type: "message",
      status: "completed",
      role: "assistant",
      phase: "final_answer",
      content: [{
        type: "output_text",
        text: JSON.stringify({ contextSha256, dialogueText: secretBait, actionChoiceId: null }),
        annotations: [],
      }],
    }],
    usage: {
      input_tokens: 20,
      input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 10,
      output_tokens_details: { reasoning_tokens: 0 },
      total_tokens: 30,
    },
    background: false,
    store: false,
    truncation: "disabled",
    max_output_tokens: NPC_COGNITION_LIMITS.maxOutputTokens,
    instructions: NPC_COGNITION_TRUSTED_INSTRUCTIONS,
    previous_response_id: null,
    conversation: null,
    prompt: null,
    tools: [],
    tool_choice: "auto",
    parallel_tool_calls: true,
    metadata: {},
    service_tier: "default",
    text: { format: structuredClone(request.text.format) },
  };
}

function jsonResponse(value) {
  return new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });
}

function reverseRootKeys(value) {
  return Object.fromEntries(Object.entries(value).reverse());
}

function assertChecks(result, { failed = [], passed = [], notChecked = [] }) {
  const checks = result.responseDiagnostic?.checks;
  assert.ok(Array.isArray(checks), "root-valid response failures expose the fixed checks array");
  assert.deepEqual(checks.map(({ rule }) => rule), CHECK_RULES);
  for (const entry of checks) {
    assert.deepEqual(Object.keys(entry), ["rule", "status"]);
    assert.ok(CHECK_STATES.has(entry.status));
  }
  const statuses = Object.fromEntries(checks.map(({ rule, status }) => [rule, status]));
  for (const rule of failed) assert.equal(statuses[rule], "failed", `${rule} must fail`);
  for (const rule of passed) assert.equal(statuses[rule], "passed", `${rule} must pass`);
  for (const rule of notChecked) assert.equal(statuses[rule], "not_checked", `${rule} must be not_checked`);
  assert.deepEqual(checks.filter(({ status }) => status === "failed").map(({ rule }) => rule), failed);
  const serialized = JSON.stringify(result.responseDiagnostic);
  for (const forbidden of [secretBait, responseIdBait, messageIdBait, choiceId,
    JSON.parse(signedFixture().callPlanJson).contextSha256]) {
    assert.equal(serialized.includes(forbidden), false, "diagnostic must not retain payload, dialogue, IDs, or bait");
  }
}

async function executeOnce(input, envelope) {
  let fetchCount = 0;
  const provider = createOpenAiNpcCognitionProvider({
    apiKey: secretBait,
    fetchImplementation: async (_endpoint, options) => {
      fetchCount += 1;
      assert.equal(options.body, input.providerRequestJson);
      return jsonResponse(envelope);
    },
  });
  const result = await executeApprovedNpcCognitionTurn(input, provider);
  assert.equal(fetchCount, 1, "each response case dispatches exactly once");
  const replay = await executeApprovedNpcCognitionTurn(input, provider);
  assert.equal(fetchCount, 1, "the same approval never dispatches twice");
  assert.equal(replay.ok, false);
  assert.equal(replay.diagnosticCode, "R22_CALL_IN_FLIGHT");
  assert.equal(replay.requestCount, 0);
  return result;
}

test("echo details distinguish rejected tool usage from normalized null description", async () => {
  // A synthetic counterexample, not the contents of any discarded real response.
  const input = signedFixture(), body = validEnvelope(input);
  body.tool_usage = null;
  body.text.format.description = null;
  const result = await executeOnce(input, body);
  assert.equal(result.ok, false);
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.equal(result.responseDiagnostic.stage, "root_echo");
  assert.equal(result.costUncertain, true);
  assert.equal(result.usage, null);
  assert.equal(result.returnedModel, null);
  assert.equal(result.actualCostMicrousd, null);
  assert.equal(result.proposal, undefined);
  assertChecks(result, { failed: ["echo_tool_usage"], passed: ["echo_text_format"] });
  assert.deepEqual(result.responseDiagnostic.echoDetails, {
    toolUsage: { jsonType: "null", objectRecord: "failed", emptyRecord: "not_checked" },
    textFormat: {
      textJsonType: "object", formatJsonType: "object", descriptionJsonType: "null",
      textRecord: "passed", textRequiredKeys: "passed", textAllowedKeys: "passed",
      formatRecord: "passed", formatRequiredKeys: "passed", formatAllowedKeys: "passed",
      typeJsonSchema: "passed", nameExact: "passed", strictTrue: "passed",
      descriptionString: "failed", descriptionSafeText: "not_checked", descriptionUtf8Limit: "not_checked",
    },
  });
});

const BASE_TEXT_DETAILS = Object.freeze({
  textJsonType: "object", formatJsonType: "object", descriptionJsonType: "absent",
  textRecord: "passed", textRequiredKeys: "passed", textAllowedKeys: "passed",
  formatRecord: "passed", formatRequiredKeys: "passed", formatAllowedKeys: "passed",
  typeJsonSchema: "passed", nameExact: "passed", strictTrue: "passed",
  descriptionString: "not_checked", descriptionSafeText: "not_checked", descriptionUtf8Limit: "not_checked",
});
const UNCHECKED_TEXT_DETAILS = Object.freeze(Object.fromEntries(Object.keys(BASE_TEXT_DETAILS)
  .map((key) => [key, key.endsWith("JsonType") ? "unavailable" : "not_checked"])));
const ABSENT_TOOL_DETAILS = Object.freeze({ jsonType: "absent", objectRecord: "not_checked", emptyRecord: "not_checked" });
const JSON_TYPES = new Set(["absent", "unavailable", "null", "array", "object", "string", "number", "boolean"]);

function assertEchoDetails(result, expected) {
  assert.deepEqual(result.responseDiagnostic.echoDetails, expected);
  assert.deepEqual(Object.keys(expected), ["toolUsage", "textFormat"]);
  assert.deepEqual(Object.keys(result.responseDiagnostic.echoDetails.toolUsage), Object.keys(ABSENT_TOOL_DETAILS));
  assert.deepEqual(Object.keys(result.responseDiagnostic.echoDetails.textFormat), Object.keys(BASE_TEXT_DETAILS));
  for (const section of Object.values(result.responseDiagnostic.echoDetails)) {
    for (const [key, value] of Object.entries(section)) {
      assert.ok((key === "jsonType" || key.endsWith("JsonType") ? JSON_TYPES : CHECK_STATES).has(value));
    }
  }
  const frozen = (value) => {
    if (value === null || typeof value !== "object") return;
    assert.equal(Object.isFrozen(value), true);
    Object.values(value).forEach(frozen);
  };
  frozen(result);
  assert.ok(Buffer.byteLength(JSON.stringify(result.responseDiagnostic)) < 4096);
}

test("tool usage diagnostic matrix keeps absent and empty records as the only accepted cases", async (t) => {
  const cases = [
    ["absent", undefined, "absent", "not_checked", "not_checked", true],
    ["null", null, "null", "failed", "not_checked", false],
    ["array", [], "array", "failed", "not_checked", false],
    ["string", secretBait, "string", "failed", "not_checked", false],
    ["number", 0, "number", "failed", "not_checked", false],
    ["boolean", false, "boolean", "failed", "not_checked", false],
    ["empty record", {}, "object", "passed", "passed", true],
    ["nonempty record", { count: 0 }, "object", "passed", "failed", false],
    ["unsafe key", { [secretBait]: { nested: secretBait } }, "object", "passed", "failed", false],
    ["own prototype key", Object.fromEntries([["__proto__", secretBait]]), "object", "passed", "failed", false],
  ];
  for (const [name, value, jsonType, objectRecord, emptyRecord, accepted] of cases) {
    await t.test(name, async () => {
      const input = signedFixture(), body = validEnvelope(input);
      if (value !== undefined) body.tool_usage = value;
      const original = await executeOnce(input, body);
      assert.equal(original.ok, accepted);
      if (accepted) assert.equal(original.responseDiagnostic, undefined);
      else {
        assert.equal(original.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
        assert.equal(original.responseDiagnostic.stage, "root_echo");
        assert.equal(original.costUncertain, true);
        assert.equal(original.usage, null);
        assertChecks(original, { failed: ["echo_tool_usage"] });
      }
      // A separate unrelated rejection observes otherwise accepted optional echoes.
      body.instructions = secretBait;
      const observed = await executeOnce(input, body);
      assertChecks(observed, { failed: accepted ? ["echo_instructions"] : ["echo_tool_usage", "echo_instructions"] });
      assertEchoDetails(observed, { toolUsage: { jsonType, objectRecord, emptyRecord }, textFormat: BASE_TEXT_DETAILS });
    });
  }
});

test("text format diagnostic matrix reports shape and subrules while retaining every acceptance boundary", async (t) => {
  const cases = [];
  const add = (name, mutate, expected, failed = ["echo_text_format"]) => cases.push({ name, mutate, expected, failed });
  add("text absent", (body) => { delete body.text; }, { ...UNCHECKED_TEXT_DETAILS, textJsonType: "absent" }, []);
  for (const [value, type] of [[null, "null"], [[], "array"], [secretBait, "string"], [0, "number"], [false, "boolean"]]) {
    add(`text ${type}`, (body) => { body.text = value; },
      { ...UNCHECKED_TEXT_DETAILS, textJsonType: type, textRecord: "failed" });
    add(`format ${type}`, (body) => { body.text.format = value; }, {
      ...UNCHECKED_TEXT_DETAILS, textJsonType: "object", formatJsonType: type,
      textRecord: "passed", textRequiredKeys: "passed", textAllowedKeys: "passed", formatRecord: "failed",
    });
  }
  add("format absent", (body) => { delete body.text.format; }, {
    ...UNCHECKED_TEXT_DETAILS, textJsonType: "object", textRecord: "passed",
    textRequiredKeys: "failed", textAllowedKeys: "passed",
  });
  add("text unknown key", (body) => { body.text[secretBait] = secretBait; }, {
    ...UNCHECKED_TEXT_DETAILS, textJsonType: "object", textRecord: "passed",
    textRequiredKeys: "passed", textAllowedKeys: "failed",
  });
  const badFormatKeys = {
    ...UNCHECKED_TEXT_DETAILS, textJsonType: "object", formatJsonType: "object",
    textRecord: "passed", textRequiredKeys: "passed", textAllowedKeys: "passed", formatRecord: "passed",
    formatRequiredKeys: "passed", formatAllowedKeys: "passed",
  };
  for (const key of ["type", "name", "strict", "schema"]) {
    add(`format missing ${key}`, (body) => { delete body.text.format[key]; },
      { ...badFormatKeys, formatRequiredKeys: "failed" });
  }
  for (const key of [secretBait, "__proto__", "constructor"]) {
    add(`format unknown ${key === secretBait ? "bait" : key}`, (body) => {
      body.text.format = Object.fromEntries([...Object.entries(body.text.format), [key, secretBait]]);
    }, { ...badFormatKeys, formatAllowedKeys: "failed" });
  }
  for (const [key, value, rule] of [["type", "text", "typeJsonSchema"], ["name", secretBait, "nameExact"],
    ["strict", false, "strictTrue"], ["strict", "true", "strictTrue"]]) {
    add(`format ${key} mismatched ${typeof value}`, (body) => { body.text.format[key] = value; },
      { ...BASE_TEXT_DETAILS, [rule]: "failed" });
  }
  add("valid format", () => {}, BASE_TEXT_DETAILS, []);
  add("schema mismatch remains separately diagnosed", (body) => { body.text.format.schema = {}; },
    BASE_TEXT_DETAILS, ["echo_schema"]);
  for (const [name, value, type, safe, bounded, accepted] of [
    ["null", null, "null", "not_checked", "not_checked", true],
    ["array", [], "array", "not_checked", "not_checked", false],
    ["object", { [secretBait]: secretBait }, "object", "not_checked", "not_checked", false],
    ["number", 0, "number", "not_checked", "not_checked", false],
    ["boolean", true, "boolean", "not_checked", "not_checked", false],
    ["empty", "", "string", "passed", "passed", true],
    ["ordinary NFC", secretBait, "string", "passed", "passed", true],
    ["line feed", "first\nsecond", "string", "passed", "passed", true],
    ["NFD", "e\u0301", "string", "failed", "passed", false],
    ["C0 control", "bad\u0000", "string", "failed", "passed", false],
    ["C1 control", "bad\u0085", "string", "failed", "passed", false],
    ["bidi control", "bad\u202e", "string", "failed", "passed", false],
    ["lone surrogate", "bad\ud800", "string", "failed", "passed", false],
    ["2048 UTF8 bytes", "é".repeat(1024), "string", "passed", "passed", true],
    ["2049 UTF8 bytes", `${"é".repeat(1024)}x`, "string", "passed", "failed", false],
  ]) {
    add(`description ${name}`, (body) => { body.text.format.description = value; }, {
      ...BASE_TEXT_DETAILS, descriptionJsonType: type, descriptionString: type === "string" ? "passed" : "failed",
      descriptionSafeText: safe, descriptionUtf8Limit: bounded,
    }, accepted ? [] : ["echo_text_format"]);
  }
  for (const row of cases) {
    await t.test(row.name, async () => {
      const input = signedFixture(), body = validEnvelope(input);
      row.mutate(body);
      const original = await executeOnce(input, body);
      assert.equal(original.ok, row.failed.length === 0);
      if (original.ok) assert.equal(original.responseDiagnostic, undefined);
      else {
        assert.equal(original.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
        assert.equal(original.responseDiagnostic.stage, row.failed[0] === "echo_schema" ? "schema_echo" : "text_format");
        assert.equal(original.costUncertain, true);
        assert.equal(original.usage, null);
        assertChecks(original, { failed: row.failed });
      }
      body.instructions = secretBait;
      const observed = await executeOnce(input, body);
      assert.equal(observed.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
      assert.equal(observed.responseDiagnostic.stage, "root_echo");
      assertChecks(observed, { failed: ["echo_instructions", ...row.failed] });
      assertEchoDetails(observed, { toolUsage: ABSENT_TOOL_DETAILS, textFormat: row.expected });
    });
  }
});

test("echo observations are byte stable for twenty key permutations and never copy unknown nested keys or counts", async () => {
  const input = signedFixture(), outputs = [];
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const body = validEnvelope(input);
    body.tool_usage = Object.fromEntries(Array.from({ length: attempt + 1 }, (_, index) => [`${secretBait}-${index}`, secretBait]));
    body.text.format = reverseRootKeys({ ...body.text.format, description: `${secretBait}-${attempt}`,
      [secretBait]: { [responseIdBait]: secretBait } });
    body.text = reverseRootKeys(body.text);
    const result = await executeOnce(input, attempt % 2 ? reverseRootKeys(body) : body);
    assertChecks(result, { failed: ["echo_tool_usage", "echo_text_format"], notChecked: ["echo_schema"] });
    const details = result.responseDiagnostic.echoDetails;
    assert.equal(details.toolUsage.emptyRecord, "failed");
    assert.equal(details.textFormat.formatAllowedKeys, "failed");
    assert.equal(details.textFormat.descriptionJsonType, "unavailable");
    assert.equal(details.textFormat.descriptionString, "not_checked");
    assertEchoDetails(result, details);
    outputs.push(canonicalizeJsonValue(result));
  }
  assert.ok(outputs.every((value) => value === outputs[0]));
});

test("diagnostic construction failure cannot replace the original rejection or permit a retry", async (t) => {
  const input = signedFixture(), body = validEnvelope(input);
  body.tool_usage = null;
  body.text.format.description = "synthetic-observer-only-marker";
  const originalEncode = TextEncoder.prototype.encode;
  let descriptionEncodes = 0;
  t.mock.method(TextEncoder.prototype, "encode", function (value) {
    if (value === body.text.format.description && ++descriptionEncodes === 2) throw new Error(secretBait);
    return originalEncode.call(this, value);
  });
  const result = await executeOnce(input, body);
  assert.equal(descriptionEncodes, 2);
  assert.equal(result.ok, false);
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.equal(result.responseDiagnostic.stage, "root_echo");
  assert.equal(result.costUncertain, true);
  assert.equal(result.usage, null);
  assert.equal(result.proposal, undefined);
  assertChecks(result, { failed: ["echo_tool_usage"] });
  assert.equal(result.responseDiagnostic.echoDetails, undefined);
});

test("request service tier is approval-bound and every legacy or non-default value fails before dispatch", async (t) => {
  const baseline = signedFixture();
  const cases = [
    ["absent", (request) => { delete request.service_tier; }],
    ["auto", (request) => { request.service_tier = "auto"; }],
    ["priority", (request) => { request.service_tier = "priority"; }],
    ["flex", (request) => { request.service_tier = "flex"; }],
    ["null", (request) => { request.service_tier = null; }],
    ["wrong type", (request) => { request.service_tier = ["default"]; }],
  ];
  for (const [name, mutate] of cases) {
    await t.test(name, async () => {
      let fetchCount = 0;
      const input = resign(baseline, mutate);
      const provider = createOpenAiNpcCognitionProvider({ apiKey: secretBait, fetchImplementation: async () => {
        fetchCount += 1;
        throw new Error("unreachable synthetic request");
      } });
      const result = await executeApprovedNpcCognitionTurn(input, provider);
      assert.equal(result.ok, false);
      assert.equal(result.diagnosticCode, "R22_APPROVAL_MISMATCH");
      assert.equal(result.requestCount, 0);
      assert.equal(result.costUncertain, false);
      assert.equal(fetchCount, 0);
    });
  }

  await t.test("adding default while retaining the legacy approval", async () => {
    const legacy = resign(baseline, (request) => { delete request.service_tier; });
    const request = JSON.parse(legacy.providerRequestJson);
    const callPlan = JSON.parse(legacy.callPlanJson);
    request.service_tier = "default";
    const providerRequestJson = canonicalizeJsonValue(request);
    callPlan.providerPayloadSha256 = sha256(providerRequestJson);
    callPlan.requestBytes = new TextEncoder().encode(providerRequestJson).byteLength;
    const mismatched = { ...legacy, providerRequestJson, callPlanJson: canonicalizeJsonValue(callPlan) };
    let fetchCount = 0;
    const provider = createOpenAiNpcCognitionProvider({ apiKey: secretBait, fetchImplementation: async () => {
      fetchCount += 1;
      throw new Error("unreachable synthetic request");
    } });
    const result = await executeApprovedNpcCognitionTurn(mismatched, provider);
    assert.equal(result.diagnosticCode, "R22_APPROVAL_MISMATCH");
    assert.equal(result.requestCount, 0);
    assert.equal(fetchCount, 0);
  });
});

test("compatible response metadata, final phase, and optional format description remain narrowly accepted", async (t) => {
  const cases = [
    ["metadata absent", (body) => { delete body.metadata; }],
    ["metadata null", (body) => { body.metadata = null; }],
    ["metadata empty", () => {}],
    ["phase absent", (body) => { delete body.output[0].phase; }],
    ["phase null", (body) => { body.output[0].phase = null; }],
    ["phase final", () => {}],
    ["description empty", (body) => { body.text.format.description = ""; }],
    ["description NFC", (body) => { body.text.format.description = "Synthetic response format"; }],
    ["description exactly 2048 UTF8 bytes", (body) => { body.text.format.description = "é".repeat(1024); }],
    ["all neutral", (body) => {
      body.metadata = null;
      body.output[0].phase = null;
      body.text.format.description = "Synthetic response format";
    }],
  ];
  for (const [name, mutate] of cases) {
    await t.test(name, async () => {
      const input = signedFixture();
      const body = validEnvelope(input);
      mutate(body);
      const result = await executeOnce(input, body);
      assert.equal(result.ok, true);
      assert.equal(result.requestCount, 1);
      assert.equal(result.costUncertain, false);
      assert.equal(Object.hasOwn(result, "responseDiagnostic"), false);
    });
  }
});

const REJECTION_CASES = [
  { name: "service tier absent", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_service_tier"], mutate: (body) => { delete body.service_tier; } },
  { name: "service tier null", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_service_tier"], mutate: (body) => { body.service_tier = null; } },
  { name: "service tier auto", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_service_tier"], mutate: (body) => { body.service_tier = "auto"; } },
  { name: "service tier priority", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_service_tier"], mutate: (body) => { body.service_tier = "priority"; } },
  { name: "service tier flex", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_service_tier"], mutate: (body) => { body.service_tier = "flex"; } },
  { name: "service tier wrong type", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_service_tier"], mutate: (body) => { body.service_tier = 1; } },
  { name: "frequency penalty", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_frequency_penalty"], mutate: (body) => { body.frequency_penalty = 1; } },
  { name: "presence penalty", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_presence_penalty"], mutate: (body) => { body.presence_penalty = 1; } },
  { name: "tool usage", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_tool_usage"], mutate: (body) => { body.tool_usage = { count: 0 }; } },
  { name: "background", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_background"], mutate: (body) => { body.background = true; } },
  { name: "store", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_store"], mutate: (body) => { body.store = true; } },
  { name: "truncation", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_truncation"], mutate: (body) => { body.truncation = "auto"; } },
  { name: "max output tokens", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_max_output_tokens"], mutate: (body) => { body.max_output_tokens -= 1; } },
  { name: "previous response", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_previous_response_id"], mutate: (body) => { body.previous_response_id = responseIdBait; } },
  { name: "conversation", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_conversation"], mutate: (body) => { body.conversation = {}; } },
  { name: "prompt", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_prompt"], mutate: (body) => { body.prompt = {}; } },
  { name: "tools", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_tools"], mutate: (body) => { body.tools = [{ type: "function" }]; } },
  { name: "tool choice", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_tool_choice"], mutate: (body) => { body.tool_choice = "required"; } },
  { name: "parallel tool calls", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_parallel_tool_calls"], mutate: (body) => { body.parallel_tool_calls = "true"; } },
  { name: "metadata non-empty", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_metadata"], mutate: (body) => { body.metadata = { bait: secretBait }; } },
  { name: "commentary phase", stage: "message", code: "R22_UNTRUSTED_OUTPUT_REJECTED",
    failed: ["message"], costUncertain: false,
    mutate: (body) => { body.output[0].phase = "commentary"; } },
  { name: "description decomposed", stage: "text_format", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_text_format"], mutate: (body) => { body.text.format.description = "e\u0301"; } },
  { name: "description control", stage: "text_format", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_text_format"], mutate: (body) => { body.text.format.description = "bad\u0000value"; } },
  { name: "description wrong type", stage: "text_format", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_text_format"], mutate: (body) => { body.text.format.description = 1; } },
  { name: "description 2049 UTF8 bytes", stage: "text_format", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_text_format"], mutate: (body) => { body.text.format.description = `${"é".repeat(1024)}x`; } },
  { name: "instructions array", stage: "root_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_instructions"], mutate: (body) => { body.instructions = [NPC_COGNITION_TRUSTED_INSTRUCTIONS]; } },
  { name: "schema strict weakened", stage: "text_format", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_text_format"], mutate: (body) => { body.text.format.strict = false; } },
  { name: "schema changed", stage: "schema_echo", code: "R22_PROVIDER_RESPONSE_INVALID",
    failed: ["echo_schema"], mutate: (body) => { body.text.format.schema.properties.dialogueText.maxLength -= 1; } },
  { name: "usage missing", stage: "usage", code: "R22_PROVIDER_USAGE_INVALID",
    failed: ["usage"], mutate: (body) => { delete body.usage.total_tokens; } },
  { name: "model mismatch", stage: "model", code: "R22_PROVIDER_MODEL_MISMATCH",
    failed: ["model"],
    mutate: (body) => { body.model = "synthetic-model"; } },
  { name: "tool output", stage: "output", code: "R22_UNTRUSTED_OUTPUT_REJECTED",
    failed: ["output"], costUncertain: false,
    mutate: (body) => { body.output.push({ type: "tool_call" }); } },
  { name: "forged choice", stage: "action_choice", code: "R22_ACTION_CHOICE_UNKNOWN",
    failed: ["action_choice"], costUncertain: false, mutate: (body) => {
      const proposal = JSON.parse(body.output[0].content[0].text);
      proposal.actionChoiceId = `choice-${"b".repeat(64)}`;
      body.output[0].content[0].text = JSON.stringify(proposal);
    } },
];

test("response compatibility rejection matrix preserves the first stage and records each failed rule", async (t) => {
  for (const row of REJECTION_CASES) {
    await t.test(row.name, async () => {
      const input = signedFixture();
      const body = validEnvelope(input);
      row.mutate(body);
      const result = await executeOnce(input, body);
      assert.equal(result.ok, false);
      assert.equal(result.diagnosticCode, row.code);
      assert.equal(result.responseDiagnostic.stage, row.stage);
      assert.equal(result.costUncertain, row.costUncertain ?? true);
      if (row.failed.includes("echo_service_tier") || row.failed.includes("model")) {
        assert.equal(result.actualCostMicrousd, null);
        assert.equal(result.usage, null);
      }
      assertChecks(result, { failed: row.failed });
    });
  }
});

test("independent failures are all reported without root echo masking later checks", async () => {
  const input = signedFixture();
  const body = validEnvelope(input);
  body.instructions = [NPC_COGNITION_TRUSTED_INSTRUCTIONS];
  body.model = "synthetic-model";
  delete body.usage.total_tokens;
  body.output[0].phase = "commentary";
  const proposal = JSON.parse(body.output[0].content[0].text);
  proposal.actionChoiceId = `choice-${"b".repeat(64)}`;
  body.output[0].content[0].text = JSON.stringify(proposal);
  const result = await executeOnce(input, body);
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.equal(result.responseDiagnostic.stage, "root_echo");
  assertChecks(result, { failed: ["echo_instructions", "usage", "model", "message", "action_choice"] });
});

test("metadata, text-format strictness, and schema drift are diagnosed together in fixed order", async () => {
  const input = signedFixture();
  const body = validEnvelope(input);
  body.metadata = { bait: secretBait };
  body.text.format.strict = false;
  body.text.format.schema.properties.dialogueText.maxLength -= 1;
  const result = await executeOnce(input, body);
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.equal(result.responseDiagnostic.stage, "root_echo");
  assertChecks(result, { failed: ["echo_metadata", "echo_text_format", "echo_schema"] });
});

test("unsafe child shapes become not_checked rather than leaking or inventing validation", async () => {
  const input = signedFixture();
  const body = validEnvelope(input);
  body.output = [null];
  const result = await executeOnce(input, body);
  assert.equal(result.responseDiagnostic.stage, "message");
  assertChecks(result, {
    failed: ["message"],
    passed: ["output"],
    notChecked: ["content", "proposal_json", "proposal_context", "dialogue_text", "action_choice"],
  });
});

test("output, content, and proposal shape dependencies each stop only unsafe descendants", async (t) => {
  const rows = [
    {
      name: "output is not an array",
      mutate: (body) => { body.output = null; },
      stage: "output",
      failed: ["output"],
      notChecked: ["message", "content", "proposal_json", "proposal_context", "dialogue_text", "action_choice"],
    },
    {
      name: "content is not an array",
      mutate: (body) => { body.output[0].content = null; },
      stage: "content",
      failed: ["content"],
      passed: ["output", "message"],
      notChecked: ["proposal_json", "proposal_context", "dialogue_text", "action_choice"],
    },
    {
      name: "proposal is not JSON",
      mutate: (body) => { body.output[0].content[0].text = "synthetic non-json"; },
      stage: "proposal_json",
      failed: ["proposal_json"],
      passed: ["output", "message", "content"],
      notChecked: ["proposal_context", "dialogue_text", "action_choice"],
    },
  ];
  for (const row of rows) {
    await t.test(row.name, async () => {
      const input = signedFixture();
      const body = validEnvelope(input);
      row.mutate(body);
      const result = await executeOnce(input, body);
      assert.equal(result.responseDiagnostic.stage, row.stage);
      assertChecks(result, row);
    });
  }
});

test("root shape and JSON failures keep their existing bounded diagnostics without checks", async (t) => {
  const input = signedFixture();
  for (const [name, response, stage] of [
    ["root", { object: "response", unknown_bait: secretBait }, "root_fields"],
    ["json", null, "json"],
  ]) {
    await t.test(name, async () => {
      const result = await executeOnce(input, response);
      assert.equal(result.ok, false);
      assert.equal(result.responseDiagnostic.stage, stage);
      assert.equal(Object.hasOwn(result.responseDiagnostic, "checks"), false);
      assert.equal(JSON.stringify(result.responseDiagnostic).includes(secretBait), false);
    });
  }
});

test("twenty canonical executions are byte-identical and successful results add no diagnostic", async () => {
  const canonicalResults = [];
  for (let index = 0; index < 20; index += 1) {
    const input = signedFixture();
    const result = await executeOnce(input, validEnvelope(input));
    assert.equal(result.ok, true);
    assert.equal(Object.hasOwn(result, "responseDiagnostic"), false);
    canonicalResults.push(canonicalizeJsonValue(result));
  }
  assert.equal(new Set(canonicalResults).size, 1);
});

test("twenty representative failures are canonical under root key reordering", async () => {
  const outputs = [];
  for (let index = 0; index < 20; index += 1) {
    const input = signedFixture();
    const body = validEnvelope(input);
    body.metadata = { bait: secretBait };
    body.model = "synthetic-model";
    const ordered = index % 2 === 0 ? body : reverseRootKeys(body);
    const result = await executeOnce(input, ordered);
    assertChecks(result, { failed: ["echo_metadata", "model"] });
    outputs.push(canonicalizeJsonValue(result));
  }
  assert.equal(new Set(outputs).size, 1);
});

test("a null-only action enum accepts a no-action proposal without weakening schema locks", async () => {
  const input = signedFixture({ candidateChoices: [] });
  const body = validEnvelope(input);
  assert.deepEqual(body.text.format.schema.properties.actionChoiceId.enum, [null]);
  const result = await executeOnce(input, body);
  assert.equal(result.ok, true);
  assert.equal(result.proposal.actionChoiceId, null);
  assert.equal(Object.hasOwn(result, "responseDiagnostic"), false);
});
