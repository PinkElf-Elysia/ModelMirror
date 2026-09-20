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

// Protocol oracle checked on 2026-09-19 against the official Responses create
// reference at developers.openai.com/api/reference/python/resources/responses/methods/create
// as documented in docs/R22_REFERENCE_AUDIT.md; the reference is not a retained response.
// Reasoning.context is independently fixed here to auto/current_turn/all_turns;
// gpt-5.6 defaults to all_turns. This suite must not derive those semantics from
// the production response allowlist or another local response test.
const REASONING_CONTEXT_VALUES = Object.freeze(["auto", "current_turn", "all_turns"]);
const approvedChoiceId = `choice-${"a".repeat(64)}`;
const unauthorizedChoiceId = `choice-${"b".repeat(64)}`;
const syntheticCredential = "offline-protocol-oracle-placeholder";
const privatePlayerText = "SYNTHETIC_PRIVATE_PLAYER_TEXT";
const privateResponseId = "resp_SYNTHETIC_PRIVATE_RESPONSE_ID";
const privateMessageId = "msg_SYNTHETIC_PRIVATE_MESSAGE_ID";
const privateResponseValue = "SYNTHETIC_PRIVATE_RESPONSE_VALUE";
const FIELD_POLICY_DIAGNOSTIC_RULES = Object.freeze([
  "billing_absent_or_null",
  "reasoning_closed_record_or_null",
  "reasoning_effort_none",
  "reasoning_mode_standard",
  "reasoning_context_supported",
  "reasoning_summary_supported",
  "reasoning_generate_summary_supported",
]);

const sha256 = (text) => `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
const fixedSha256 = (character) => `sha256:${character.repeat(64)}`;

function createApprovedInput() {
  const providerInput = canonicalizeJsonValue({
    playerText: privatePlayerText,
    state: { actor: "npc-protocol-oracle", visibility: "local" },
  });
  const contextSha256 = sha256(providerInput);
  const candidateChoices = [{
    choiceId: approvedChoiceId,
    intentSha256: fixedSha256("a"),
  }];
  const responseSchema = {
    type: "object",
    additionalProperties: false,
    required: ["contextSha256", "dialogueText", "actionChoiceId"],
    properties: {
      contextSha256: { type: "string", const: contextSha256 },
      dialogueText: {
        type: "string",
        minLength: 1,
        maxLength: NPC_COGNITION_LIMITS.dialogueBytes,
      },
      actionChoiceId: {
        type: ["string", "null"],
        enum: [null, approvedChoiceId],
      },
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
    text: {
      format: {
        type: "json_schema",
        name: "matrix_oasis_npc_dialogue_proposal",
        strict: true,
        schema: responseSchema,
      },
    },
    truncation: "disabled",
  };
  const providerRequestJson = canonicalizeJsonValue(request);
  const callPlan = {
    format: NPC_COGNITION_CALL_PLAN_FORMAT,
    formatVersion: NPC_COGNITION_FORMAT_VERSION,
    canonicalization: NPC_COGNITION_CANONICALIZATION,
    turnId: "turn-response-protocol-oracle",
    turnSha256: fixedSha256("2"),
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
    approval: {
      hash: fixedSha256("0"),
      expiresAfterMs: NPC_COGNITION_LIMITS.approvalLifetimeMs,
    },
  };
  callPlan.approval.hash = computeNpcCognitionApprovalHash(callPlan);
  return {
    approvalHash: callPlan.approval.hash,
    callPlanJson: canonicalizeJsonValue(callPlan),
    providerRequestJson,
  };
}

function createSyntheticResponse(input) {
  const request = JSON.parse(input.providerRequestJson);
  const contextSha256 = JSON.parse(input.callPlanJson).contextSha256;
  return {
    // Synthetic protocol fixture only: not a retained real response, invoice,
    // billing record, or evidence that any particular provider response occurred.
    id: privateResponseId,
    object: "response",
    status: "completed",
    error: null,
    incomplete_details: null,
    model: NPC_COGNITION_MODEL,
    output: [{
      id: privateMessageId,
      type: "message",
      status: "completed",
      role: "assistant",
      phase: "final_answer",
      content: [{
        type: "output_text",
        text: JSON.stringify({
          contextSha256,
          dialogueText: "Protocol oracle response.",
          actionChoiceId: approvedChoiceId,
        }),
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
    instructions: NPC_COGNITION_TRUSTED_INSTRUCTIONS,
    max_output_tokens: NPC_COGNITION_LIMITS.maxOutputTokens,
    metadata: {},
    parallel_tool_calls: true,
    previous_response_id: null,
    conversation: null,
    service_tier: "default",
    store: false,
    text: { format: structuredClone(request.text.format) },
    tool_choice: "auto",
    tools: [],
    truncation: "disabled",
  };
}

function setProposal(response, patch) {
  const content = response.output[0].content[0];
  content.text = JSON.stringify({ ...JSON.parse(content.text), ...patch });
}

function assertPrivateValuesAbsent(result) {
  const serialized = JSON.stringify(result);
  for (const value of [
    syntheticCredential,
    privatePlayerText,
    privateResponseId,
    privateMessageId,
    privateResponseValue,
    unauthorizedChoiceId,
  ]) {
    assert.equal(serialized.includes(value), false, `result must not retain ${value}`);
  }
  for (const value of ["future_turn", "pro", "high", "synthetic_unverified_value"]) {
    assert.equal(serialized.includes(JSON.stringify(value)), false,
      `result must not retain the response scalar or object key ${value}`);
  }
}

function assertFixedFieldPolicyDiagnostic(result) {
  const checks = result.responseDiagnostic.fieldPolicyChecks;
  assert.deepEqual(checks.map(({ rule }) => rule), FIELD_POLICY_DIAGNOSTIC_RULES);
  for (const check of checks) {
    assert.deepEqual(Object.keys(check), ["rule", "status", "jsonType"]);
    assert.ok(["passed", "failed", "not_checked"].includes(check.status));
    assert.ok(["absent", "null", "array", "object", "string", "number", "boolean", "unavailable"]
      .includes(check.jsonType));
  }
}

async function executeSynthetic(input, response) {
  let injectedFetchCalls = 0;
  const approvedRequestJson = input.providerRequestJson;
  const approvedRequest = JSON.parse(approvedRequestJson);
  assert.equal(Object.hasOwn(approvedRequest, "conversation"), false);
  assert.equal(Object.hasOwn(approvedRequest, "previous_response_id"), false);

  const provider = createOpenAiNpcCognitionProvider({
    apiKey: syntheticCredential,
    fetchImplementation: async (endpoint, options) => {
      injectedFetchCalls += 1;
      assert.equal(endpoint, NPC_COGNITION_ENDPOINT);
      assert.equal(options.method, "POST");
      assert.equal(options.body, approvedRequestJson);
      assert.deepEqual(JSON.parse(options.body), approvedRequest);
      return new Response(JSON.stringify(response), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await executeApprovedNpcCognitionTurn(input, provider);
  assert.equal(injectedFetchCalls, 1);
  assert.equal(input.providerRequestJson, approvedRequestJson, "response metadata must not mutate the request payload");
  return result;
}

test("official Reasoning.context values preserve one stateless approved response", async (t) => {
  for (const context of REASONING_CONTEXT_VALUES) {
    await t.test(context, async () => {
      const input = createApprovedInput();
      const response = createSyntheticResponse(input);
      response.reasoning = { effort: "none", mode: "standard", context };

      const result = await executeSynthetic(input, response);

      assert.equal(result.ok, true, JSON.stringify(result.responseDiagnostic));
      assert.equal(response.conversation, null);
      assert.equal(response.previous_response_id, null);
      assert.equal(result.requestCount, 1);
      assert.equal(Object.hasOwn(result, "responseDiagnostic"), false);
      assert.deepEqual(result.proposal, {
        contextSha256: JSON.parse(input.callPlanJson).contextSha256,
        dialogueText: "Protocol oracle response.",
        actionChoiceId: approvedChoiceId,
      });
      assertPrivateValuesAbsent(result);
    });
  }
});

const REJECTION_CASES = [
  {
    name: "unknown reasoning context",
    code: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "envelope_profile",
    field: "reasoning",
    mutate(response) {
      response.reasoning = { effort: "none", mode: "standard", context: "future_turn" };
    },
  },
  {
    name: "unapproved pro reasoning mode",
    code: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "envelope_profile",
    field: "reasoning",
    mutate(response) {
      response.reasoning = { effort: "none", mode: "pro", context: "current_turn" };
    },
  },
  {
    name: "unapproved reasoning effort",
    code: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "envelope_profile",
    field: "reasoning",
    mutate(response) {
      response.reasoning = { effort: "high", mode: "standard", context: "current_turn" };
    },
  },
  {
    name: "unrequested root tool item",
    code: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "root_echo",
    mutate(response) {
      response.tools = [{ type: "function", name: privateResponseValue }];
    },
  },
  {
    name: "tool output item beside the dialogue",
    code: "R22_UNTRUSTED_OUTPUT_REJECTED",
    stage: "output",
    mutate(response) {
      response.output.push({
        id: privateResponseValue,
        type: "function_call",
        name: privateResponseValue,
        arguments: "{}",
      });
    },
  },
  {
    name: "unauthorized action choice",
    code: "R22_ACTION_CHOICE_UNKNOWN",
    stage: "action_choice",
    mutate(response) {
      setProposal(response, { actionChoiceId: unauthorizedChoiceId });
    },
  },
  {
    name: "non-null synthetic billing metadata",
    code: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "envelope_profile",
    field: "billing",
    mutate(response) {
      response.billing = { synthetic_unverified_value: privateResponseValue };
    },
  },
];

test("the protocol enum fix does not widen authority, tools, billing, mode, or effort", async (t) => {
  for (const row of REJECTION_CASES) {
    await t.test(row.name, async () => {
      const input = createApprovedInput();
      const response = createSyntheticResponse(input);
      response.reasoning = { effort: "none", mode: "standard", context: "current_turn" };
      row.mutate(response);

      const result = await executeSynthetic(input, response);

      assert.equal(result.ok, false);
      assert.equal(result.diagnosticCode, row.code);
      assert.equal(result.responseDiagnostic.stage, row.stage);
      if (row.field) {
        assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, [row.field]);
        assertFixedFieldPolicyDiagnostic(result);
      }
      assert.equal(result.requestCount, 1);
      assert.equal(Object.hasOwn(result, "proposal"), false);
      assertPrivateValuesAbsent(result);
    });
  }
});
