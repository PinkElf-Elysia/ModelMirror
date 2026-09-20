import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
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
  createNpcCognitionToolUsageDiagnosticPlan,
  createOpenAiNpcCognitionProvider,
  evaluateNpcCognitionToolUsageDiagnosticFixture,
  executeApprovedNpcCognitionTurn,
} from "../src/index.mjs";
import { observeBilling } from "../src/billing-observer.mjs";

// This conformance suite deliberately constructs its own request signatures and
// response documents. It does not import the response-matrix fixture or validator,
// so acceptance is exercised only through the package's two public entry points.
// This proves the local profile, not the upstream protocol. The separate
// response-protocol-oracle suite checks documented defaults independently.
const digest = (text) => `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
const fixedDigest = (character) => `sha256:${character.repeat(64)}`;
const approvedChoiceId = `choice-${"a".repeat(64)}`;
const forgedChoiceId = `choice-${"b".repeat(64)}`;
const privateCredential = "offline-test-credential-do-not-retain";
const privateContextText = "private-player-context-do-not-retain";
const privateEnvelopeText = "private-response-metadata-do-not-retain";

function createSignedTurn() {
  const providerInput = canonicalizeJsonValue({
    playerText: privateContextText,
    state: { actor: "npc-alpha", visibility: "local" },
  });
  const contextSha256 = digest(providerInput);
  const candidateChoices = [{
    choiceId: approvedChoiceId,
    intentSha256: fixedDigest("a"),
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
    turnId: "turn-response-conformance",
    turnSha256: fixedDigest("2"),
    contextSha256,
    candidateSha256: digest(canonicalizeJsonValue(candidateChoices)),
    candidateChoices,
    providerPayloadSha256: digest(providerRequestJson),
    responseSchemaSha256: digest(canonicalizeJsonValue(responseSchema)),
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
      hash: fixedDigest("0"),
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

function expectedProposal(input) {
  return {
    contextSha256: JSON.parse(input.callPlanJson).contextSha256,
    dialogueText: "I will stay here and keep watch.",
    actionChoiceId: approvedChoiceId,
  };
}

function createSyntheticResponse(input) {
  const request = JSON.parse(input.providerRequestJson);
  const proposal = expectedProposal(input);
  return {
    // This is a synthetic document using the public Responses layout and the
    // observed C-shape echoes. The unavailable raw response is not represented
    // here, and no test below claims this is a complete real-provider recording.
    id: "resp_synthetic_conformance",
    object: "response",
    created_at: 1_800_000_000,
    completed_at: 1_800_000_001,
    status: "completed",
    error: null,
    incomplete_details: null,
    model: NPC_COGNITION_MODEL,
    output: [{
      id: "msg_synthetic_conformance",
      type: "message",
      status: "completed",
      role: "assistant",
      phase: "final_answer",
      content: [{
        type: "output_text",
        text: JSON.stringify(proposal),
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
    service_tier: "default",
    store: false,
    text: { format: structuredClone(request.text.format) },
    tool_choice: "auto",
    tools: [],
    truncation: "disabled",
  };
}

function jsonResponse(value) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function rotateRecord(record, offset) {
  const entries = Object.entries(record);
  const pivot = entries.length === 0 ? 0 : offset % entries.length;
  return Object.fromEntries([...entries.slice(pivot), ...entries.slice(0, pivot)]);
}

function parseStaticStringArray(source, constantName) {
  const escapedName = constantName.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const declaration = new RegExp(
    `const\\s+${escapedName}\\s*=\\s*Object\\.freeze\\(\\[([\\s\\S]*?)\\]\\);`,
    "gu",
  );
  const matches = [...source.matchAll(declaration)];
  assert.equal(matches.length, 1, `${constantName} must be one static Object.freeze array`);
  const body = matches[0][1];
  const stringLiterals = body.match(/"(?:[^"\\]|\\.)*"/gu) ?? [];
  const residue = body.replace(/"(?:[^"\\]|\\.)*"/gu, "").replace(/[\s,]/gu, "");
  assert.equal(residue, "", `${constantName} must contain only JSON string literals`);
  assert.ok(stringLiterals.length > 0, `${constantName} must not be empty`);
  const fields = stringLiterals.map((literal) => JSON.parse(literal));
  assert.equal(new Set(fields).size, fields.length, `${constantName} must not repeat fields`);
  return fields;
}

async function executeOffline(input, responseDocument, { rawBody } = {}) {
  let fetchCount = 0;
  const provider = createOpenAiNpcCognitionProvider({
    apiKey: privateCredential,
    fetchImplementation: async (url, options) => {
      fetchCount += 1;
      assert.equal(url, NPC_COGNITION_ENDPOINT);
      assert.equal(options.method, "POST");
      assert.equal(options.body, input.providerRequestJson);
      assert.equal(options.headers.authorization, `Bearer ${privateCredential}`);
      return rawBody === undefined ? jsonResponse(responseDocument) : new Response(rawBody, {
        status: 200, headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await executeApprovedNpcCognitionTurn(input, provider);
  assert.equal(fetchCount, 1, "an approved turn dispatches exactly once");

  const replay = await executeApprovedNpcCognitionTurn(input, provider);
  assert.equal(fetchCount, 1, "replaying the same approval never retries or redispatches");
  assert.deepEqual(replay, {
    ok: false,
    diagnosticCode: "R22_CALL_IN_FLIGHT",
    requestCount: 0,
    costUncertain: false,
    returnedModel: null,
    usage: null,
    actualCostMicrousd: null,
  });
  return result;
}

function failedRules(result) {
  return (result.responseDiagnostic?.checks ?? [])
    .filter(({ status }) => status === "failed")
    .map(({ rule }) => rule);
}

function assertClosedProposal(result, expected) {
  assert.equal(result.ok, true, JSON.stringify(result.responseDiagnostic));
  assert.deepEqual(result.proposal, expected);
  assert.deepEqual(Object.keys(result.proposal), ["contextSha256", "dialogueText", "actionChoiceId"]);
  assert.equal(result.proposalJson, canonicalizeJsonValue(expected));
  assert.equal(Object.isFrozen(result.proposal), true);
}

function assertPrivateDataAbsent(result, extraValues = []) {
  const serialized = JSON.stringify(result);
  for (const value of [
    privateCredential,
    privateContextText,
    privateEnvelopeText,
    "resp_synthetic_conformance",
    "msg_synthetic_conformance",
    ...extraValues,
  ]) {
    assert.equal(serialized.includes(value), false, `result must not retain ${value}`);
  }
}

test("description is non-semantic: absent, null, and a bounded safe string preserve one closed proposal", async (t) => {
  const cases = [
    ["absent", (format) => { delete format.description; }, null],
    // Null was observed as a non-semantic echo. This is normalization policy,
    // not a claim that the official upstream schema has proven it nullable.
    ["null", (format) => { format.description = null; }, null],
    ["bounded safe string", (format) => { format.description = "NPC dialogue proposal."; }, "NPC dialogue proposal."],
  ];

  for (const [name, mutate, discardedDescription] of cases) {
    await t.test(name, async () => {
      const input = createSignedTurn();
      const responseDocument = createSyntheticResponse(input);
      mutate(responseDocument.text.format);
      const result = await executeOffline(input, responseDocument);

      assertClosedProposal(result, expectedProposal(input));
      assert.equal(result.requestCount, 1);
      assert.equal(result.costUncertain, false);
      assert.equal(result.returnedModel, NPC_COGNITION_MODEL);
      if (discardedDescription !== null) {
        assert.equal(JSON.stringify(result).includes(discardedDescription), false);
      }
      assertPrivateDataAbsent(result);
    });
  }
});

test("twenty non-semantic metadata variants preserve proposal and cost without retaining private response metadata", async () => {
  const stableResults = [];
  for (let index = 0; index < 20; index += 1) {
    const input = createSignedTurn();
    const responseDocument = createSyntheticResponse(input);
    const suffix = index.toString().padStart(2, "0");
    const responseId = `resp_${privateEnvelopeText}_${suffix}`;
    const messageId = `msg_${privateEnvelopeText}_${suffix}`;
    responseDocument.id = responseId;
    responseDocument.output[0].id = messageId;
    responseDocument.created_at = 1_800_000_000 + index;
    responseDocument.completed_at = 1_800_001_000 + index;
    if (index % 3 === 0) delete responseDocument.metadata;
    else if (index % 3 === 1) responseDocument.metadata = null;
    else responseDocument.metadata = {};
    if (index % 2 === 0) delete responseDocument.output[0].phase;
    else responseDocument.output[0].phase = index % 4 === 1 ? null : "final_answer";
    if (index % 2 === 0) delete responseDocument.text.format.description;
    else responseDocument.text.format.description = `Synthetic format ${suffix}`;

    const nullableCapabilities = [
      "agent",
      "billing",
      "context_management",
      "moderation",
      "prompt_cache_diagnostics",
      "max_tool_calls",
      "prompt_cache_key",
      "safety_identifier",
      "user",
    ];
    for (const [capabilityIndex, field] of nullableCapabilities.entries()) {
      if ((index + capabilityIndex) % 2 === 0) responseDocument[field] = null;
    }
    responseDocument.reasoning = rotateRecord({
      effort: "none",
      mode: "standard",
      context: index % 2 === 0 ? null : "current_turn",
      summary: [null, "auto", "concise", "detailed"][index % 4],
      generate_summary: ["detailed", "concise", "auto", null][index % 4],
    }, index);
    responseDocument.prompt_cache_options = rotateRecord({
      mode: "implicit",
      ttl: "30m",
      ...(index % 2 === 0 ? {} : { comparison_response_id: null }),
    }, index);
    responseDocument.prompt_cache_retention = [null, "in_memory", "24h"][index % 3];
    responseDocument.temperature = [null, 0, 1, 2][index % 4];
    responseDocument.top_p = [null, 0, 0.5, 1][index % 4];
    responseDocument.top_logprobs = index % 2 === 0 ? null : 0;
    responseDocument.text.verbosity = [null, "low", "medium", "high"][index % 4];
    responseDocument.text.format = rotateRecord(responseDocument.text.format, index);
    responseDocument.text = rotateRecord(responseDocument.text, index);
    const reorderedResponse = rotateRecord(responseDocument, index);

    const result = await executeOffline(input, reorderedResponse);
    assertClosedProposal(result, expectedProposal(input));
    assert.equal(result.requestCount, 1);
    assert.equal(result.costUncertain, false);
    assert.deepEqual(result.usage, {
      inputTokens: 20,
      cachedInputTokens: 0,
      cacheWriteInputTokens: 0,
      outputTokens: 10,
      totalTokens: 30,
    });
    assert.equal(result.actualCostMicrousd, 16);
    assertPrivateDataAbsent(result, [responseId, messageId, `Synthetic format ${suffix}`]);
    stableResults.push(canonicalizeJsonValue({
      ok: result.ok,
      requestCount: result.requestCount,
      costUncertain: result.costUncertain,
      returnedModel: result.returnedModel,
      usage: result.usage,
      actualCostMicrousd: result.actualCostMicrousd,
      proposal: result.proposal,
      proposalJson: result.proposalJson,
    }));
  }
  assert.equal(new Set(stableResults).size, 1);
});

test("a non-empty tool_usage object still fails closed when description is null", async () => {
  const input = createSignedTurn();
  const responseDocument = createSyntheticResponse(input);
  responseDocument.tool_usage = { count: 0 };
  responseDocument.text.format.description = null;

  const result = await executeOffline(input, responseDocument);
  assert.equal(result.ok, false);
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.equal(result.responseDiagnostic.stage, "root_echo");
  assert.deepEqual(failedRules(result), ["echo_tool_usage"]);
  assert.equal(result.requestCount, 1);
  assert.equal(result.costUncertain, true);
  assert.equal(result.usage, null);
  assert.equal(result.actualCostMicrousd, null);
  assert.equal(Object.hasOwn(result, "proposal"), false);
  assertPrivateDataAbsent(result);
});

const SAFETY_COUNTEREXAMPLES = [
  {
    name: "unknown response root field",
    diagnosticCode: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "root_fields",
    costUncertain: true,
    mutate(responseDocument) {
      responseDocument.unapproved_root = privateEnvelopeText;
    },
  },
  {
    name: "tool output beside the message",
    diagnosticCode: "R22_UNTRUSTED_OUTPUT_REJECTED",
    stage: "output",
    failedRules: ["output"],
    costUncertain: false,
    mutate(responseDocument) {
      responseDocument.output.push({
        type: "function_call",
        name: "synthetic_tool",
        arguments: "{}",
      });
    },
  },
  {
    name: "proposal has a fourth business field",
    diagnosticCode: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "proposal_json",
    failedRules: ["proposal_json"],
    costUncertain: false,
    mutate(responseDocument) {
      const proposal = JSON.parse(responseDocument.output[0].content[0].text);
      proposal.unapprovedField = true;
      responseDocument.output[0].content[0].text = JSON.stringify(proposal);
    },
  },
  {
    name: "forged but well-formed choice",
    diagnosticCode: "R22_ACTION_CHOICE_UNKNOWN",
    stage: "action_choice",
    failedRules: ["action_choice"],
    costUncertain: false,
    mutate(responseDocument) {
      const proposal = JSON.parse(responseDocument.output[0].content[0].text);
      proposal.actionChoiceId = forgedChoiceId;
      responseDocument.output[0].content[0].text = JSON.stringify(proposal);
    },
  },
  {
    name: "response schema echo drift",
    diagnosticCode: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "schema_echo",
    failedRules: ["echo_schema"],
    costUncertain: true,
    mutate(responseDocument) {
      responseDocument.text.format.schema.properties.dialogueText.maxLength -= 1;
    },
  },
  {
    name: "proposal context drift",
    diagnosticCode: "R22_UNTRUSTED_OUTPUT_REJECTED",
    stage: "proposal_context",
    failedRules: ["proposal_context"],
    costUncertain: false,
    mutate(responseDocument) {
      const proposal = JSON.parse(responseDocument.output[0].content[0].text);
      proposal.contextSha256 = fixedDigest("f");
      responseDocument.output[0].content[0].text = JSON.stringify(proposal);
    },
  },
  {
    name: "model drift",
    diagnosticCode: "R22_PROVIDER_MODEL_MISMATCH",
    stage: "model",
    failedRules: ["model"],
    costUncertain: true,
    mutate(responseDocument) {
      responseDocument.model = "synthetic-model-drift";
    },
  },
  {
    name: "non-Standard service tier",
    diagnosticCode: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "root_echo",
    failedRules: ["echo_service_tier"],
    costUncertain: true,
    mutate(responseDocument) {
      responseDocument.service_tier = "priority";
    },
  },
  {
    name: "usage arithmetic drift",
    diagnosticCode: "R22_PROVIDER_USAGE_INVALID",
    stage: "usage",
    failedRules: ["usage"],
    costUncertain: true,
    mutate(responseDocument) {
      responseDocument.usage.total_tokens += 1;
    },
  },
];

test("C-shape safety counterexamples fail closed without becoming real-response evidence", async (t) => {
  // These cases are synthetic C-shape counterexamples because the original raw
  // provider response is unavailable. They prove local rejection behavior only.
  for (const row of SAFETY_COUNTEREXAMPLES) {
    await t.test(row.name, async () => {
      const input = createSignedTurn();
      const responseDocument = createSyntheticResponse(input);
      row.mutate(responseDocument);

      const result = await executeOffline(input, responseDocument);
      assert.equal(result.ok, false);
      assert.equal(result.diagnosticCode, row.diagnosticCode);
      assert.equal(result.responseDiagnostic.stage, row.stage);
      assert.equal(result.requestCount, 1);
      assert.equal(result.costUncertain, row.costUncertain);
      if (row.failedRules) assert.deepEqual(failedRules(result), row.failedRules);
      if (row.costUncertain) {
        assert.equal(result.usage, null);
        assert.equal(result.actualCostMicrousd, null);
      } else {
        assert.equal(result.actualCostMicrousd, 16);
      }
      assert.equal(Object.hasOwn(result, "proposal"), false);
      assertPrivateDataAbsent(result);
    });
  }
});

const UNPROVEN_ROOT_CAPABILITY_COUNTEREXAMPLES = [
  "agent",
  "billing",
  "context_management",
  "moderation",
  "prompt_cache_diagnostics",
].map((field) => ({
  name: `${field} non-empty object`,
  mutate(responseDocument) {
    responseDocument[field] = { synthetic_private_value: privateEnvelopeText };
  },
}));

const ROOT_SCALAR_GUARD_COUNTEREXAMPLES = [
  {
    name: "positive max_tool_calls without an enabled tool capability",
    mutate(responseDocument) {
      responseDocument.max_tool_calls = 1;
    },
  },
  {
    name: "high reasoning effort instead of the approved none effort",
    mutate(responseDocument) {
      responseDocument.reasoning = { effort: "high" };
    },
  },
  ...["prompt_cache_key", "safety_identifier", "user"].map((field) => ({
    name: `${field} non-empty identity string`,
    mutate(responseDocument) {
      responseDocument[field] = `${privateEnvelopeText}-${field}`;
    },
  })),
  {
    name: "root response id is not a string",
    mutate(responseDocument) {
      responseDocument.id = 7;
    },
  },
  {
    name: "created_at is not an integer",
    mutate(responseDocument) {
      responseDocument.created_at = 1_800_000_000.5;
    },
  },
  {
    name: "temperature is below zero",
    mutate(responseDocument) {
      responseDocument.temperature = -0.01;
    },
  },
  {
    name: "temperature is above two",
    mutate(responseDocument) {
      responseDocument.temperature = 2.01;
    },
  },
  {
    name: "top_p is below zero",
    mutate(responseDocument) {
      responseDocument.top_p = -0.01;
    },
  },
  {
    name: "top_p is above one",
    mutate(responseDocument) {
      responseDocument.top_p = 1.01;
    },
  },
  {
    name: "top_logprobs is non-zero",
    mutate(responseDocument) {
      responseDocument.top_logprobs = 1;
    },
  },
];

test("unproven root capabilities, identities, and out-of-range scalars fail closed", async (t) => {
  for (const row of [
    ...UNPROVEN_ROOT_CAPABILITY_COUNTEREXAMPLES,
    ...ROOT_SCALAR_GUARD_COUNTEREXAMPLES,
  ]) {
    await t.test(row.name, async () => {
      const input = createSignedTurn();
      const responseDocument = createSyntheticResponse(input);
      row.mutate(responseDocument);

      const result = await executeOffline(input, responseDocument);
      assert.equal(result.requestCount, 1);
      assertPrivateDataAbsent(result);
      assert.equal(result.ok, false, "an allowlisted root name alone is not acceptance evidence");
      assert.equal(Object.hasOwn(result, "proposal"), false);
    });
  }
});

const FIELD_POLICY_POSITIVE_CASES = [
  ["id lower length boundary", (body) => { body.id = "r"; }],
  ["id upper length boundary", (body) => { body.id = "r".repeat(256); }],
  ["created_at zero", (body) => { body.created_at = 0; }],
  ["created_at maximum safe integer", (body) => { body.created_at = Number.MAX_SAFE_INTEGER; }],
  ["completed_at absent", (body) => { delete body.completed_at; }],
  ["completed_at null", (body) => { body.completed_at = null; }],
  ["completed_at zero", (body) => { body.completed_at = 0; }],
  ["completed_at maximum safe integer", (body) => { body.completed_at = Number.MAX_SAFE_INTEGER; }],
  ["unrequested capabilities and identities are explicitly null", (body) => {
    for (const field of [
      "agent",
      "billing",
      "context_management",
      "moderation",
      "prompt_cache_diagnostics",
      "max_tool_calls",
      "prompt_cache_key",
      "safety_identifier",
      "user",
    ]) body[field] = null;
  }],
  ["reasoning null", (body) => { body.reasoning = null; }],
  ["reasoning closed empty record", (body) => { body.reasoning = {}; }],
  ["reasoning full null record", (body) => { body.reasoning = {
    effort: "none", mode: "standard", context: null, summary: null, generate_summary: null,
  }; }],
  ...["auto", "concise", "detailed"].map((summary) => [
    `reasoning ${summary} summary values`,
    (body) => { body.reasoning = {
      effort: "none",
      mode: "standard",
      context: "current_turn",
      summary,
      generate_summary: summary,
    }; },
  ]),
  ["prompt cache options without comparison identity", (body) => {
    body.prompt_cache_options = { mode: "implicit", ttl: "30m" };
  }],
  ["prompt cache options with null comparison identity", (body) => {
    body.prompt_cache_options = { mode: "implicit", ttl: "30m", comparison_response_id: null };
  }],
  ...[null, "in_memory", "24h"].map((retention) => [
    `prompt cache retention ${String(retention)}`,
    (body) => { body.prompt_cache_retention = retention; },
  ]),
  ...[null, 0, 2].map((temperature) => [
    `temperature boundary ${String(temperature)}`,
    (body) => { body.temperature = temperature; },
  ]),
  ...[null, 0, 1].map((topP) => [
    `top_p boundary ${String(topP)}`,
    (body) => { body.top_p = topP; },
  ]),
  ...[null, 0].map((topLogprobs) => [
    `top_logprobs boundary ${String(topLogprobs)}`,
    (body) => { body.top_logprobs = topLogprobs; },
  ]),
  ...[null, "low", "medium", "high"].map((verbosity) => [
    `text verbosity ${String(verbosity)}`,
    (body) => { body.text.verbosity = verbosity; },
  ]),
];

test("the local response envelope profile accepts every documented neutral value and inclusive boundary", async (t) => {
  // This is the Matrix Oasis local no-tools profile, not a claim that these
  // values form the complete official Responses schema.
  for (const [name, mutate] of FIELD_POLICY_POSITIVE_CASES) {
    await t.test(name, async () => {
      const input = createSignedTurn();
      const responseDocument = createSyntheticResponse(input);
      mutate(responseDocument);

      const result = await executeOffline(input, responseDocument);
      assertClosedProposal(result, expectedProposal(input));
      assert.equal(result.requestCount, 1);
      assert.equal(result.actualCostMicrousd, 16);
      assertPrivateDataAbsent(result);
    });
  }
});

const FIELD_POLICY_NEGATIVE_CASES = [
  ["id empty", "id", (body) => { body.id = ""; }],
  ["id too long", "id", (body) => { body.id = "r".repeat(257); }],
  ["id control text", "id", (body) => { body.id = "bad\u0000id"; }],
  ["created_at null", "created_at", (body) => { body.created_at = null; }],
  ["created_at negative", "created_at", (body) => { body.created_at = -1; }],
  ["created_at fractional", "created_at", (body) => { body.created_at = 1.5; }],
  ["completed_at negative", "completed_at", (body) => { body.completed_at = -1; }],
  ["completed_at fractional", "completed_at", (body) => { body.completed_at = 1.5; }],
  ["completed_at string", "completed_at", (body) => { body.completed_at = "1"; }],
  ["reasoning array", "reasoning", (body) => { body.reasoning = []; }],
  ["reasoning unknown key", "reasoning", (body) => { body.reasoning = { private_key: privateEnvelopeText }; }],
  ["reasoning effort drift", "reasoning", (body) => { body.reasoning = { effort: "high" }; }],
  ["reasoning mode drift", "reasoning", (body) => { body.reasoning = { mode: "experimental" }; }],
  ["reasoning context drift", "reasoning", (body) => { body.reasoning = { context: "previous_turn" }; }],
  ["reasoning summary drift", "reasoning", (body) => { body.reasoning = { summary: "brief" }; }],
  ["reasoning generate_summary drift", "reasoning", (body) => { body.reasoning = { generate_summary: "brief" }; }],
  ["prompt cache options null", "prompt_cache_options", (body) => { body.prompt_cache_options = null; }],
  ["prompt cache options missing mode", "prompt_cache_options", (body) => {
    body.prompt_cache_options = { ttl: "30m" };
  }],
  ["prompt cache options missing ttl", "prompt_cache_options", (body) => {
    body.prompt_cache_options = { mode: "implicit" };
  }],
  ["prompt cache options mode drift", "prompt_cache_options", (body) => {
    body.prompt_cache_options = { mode: "explicit", ttl: "30m" };
  }],
  ["prompt cache options ttl drift", "prompt_cache_options", (body) => {
    body.prompt_cache_options = { mode: "implicit", ttl: "1h" };
  }],
  ["prompt cache comparison identity", "prompt_cache_options", (body) => {
    body.prompt_cache_options = {
      mode: "implicit", ttl: "30m", comparison_response_id: privateEnvelopeText,
    };
  }],
  ["prompt cache options unknown key", "prompt_cache_options", (body) => {
    body.prompt_cache_options = { mode: "implicit", ttl: "30m", private_key: privateEnvelopeText };
  }],
  ["prompt cache retention drift", "prompt_cache_retention", (body) => {
    body.prompt_cache_retention = "7d";
  }],
  ["temperature wrong type", "temperature", (body) => { body.temperature = "1"; }],
  ["top_p wrong type", "top_p", (body) => { body.top_p = "0.5"; }],
  ["top_logprobs wrong type", "top_logprobs", (body) => { body.top_logprobs = "0"; }],
  ["text verbosity drift", "text_verbosity", (body) => { body.text.verbosity = "verbose"; }],
];

test("each local envelope field rejects malformed, widened, or capability-enabling values", async (t) => {
  for (const [name, failureField, mutate] of FIELD_POLICY_NEGATIVE_CASES) {
    await t.test(name, async () => {
      const input = createSignedTurn();
      const responseDocument = createSyntheticResponse(input);
      mutate(responseDocument);

      const result = await executeOffline(input, responseDocument);
      assert.equal(result.ok, false);
      assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
      assert.equal(result.responseDiagnostic.stage, "envelope_profile");
      assert.equal(result.responseDiagnostic.envelopeProfile, "matrix-oasis.responses-envelope/1");
      assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, [failureField]);
      assert.equal(result.requestCount, 1);
      assert.equal(Object.hasOwn(result, "proposal"), false);
      assertPrivateDataAbsent(result);
    });
  }
});

test("field-policy failures remain fixed while prior echo, schema, and model failures stay visible", async () => {
  const input = createSignedTurn();
  const responseDocument = createSyntheticResponse(input);
  responseDocument.id = 7;
  responseDocument.created_at = 1.5;
  responseDocument.agent = { private_key: privateEnvelopeText };
  responseDocument.reasoning = { effort: "high", private_key: privateEnvelopeText };
  responseDocument.prompt_cache_options = {
    mode: "explicit",
    ttl: "forever",
    comparison_response_id: privateEnvelopeText,
  };
  responseDocument.temperature = 3;
  responseDocument.top_p = 2;
  responseDocument.top_logprobs = 1;
  responseDocument.text.verbosity = privateEnvelopeText;
  responseDocument.service_tier = "priority";
  responseDocument.text.format.schema.properties.dialogueText.maxLength -= 1;
  responseDocument.model = "synthetic-model-drift";

  const result = await executeOffline(input, responseDocument);
  assert.equal(result.ok, false);
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.equal(result.responseDiagnostic.stage, "root_echo");
  assert.equal(result.responseDiagnostic.envelopeProfile, "matrix-oasis.responses-envelope/1");
  assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, [
    "id",
    "created_at",
    "agent",
    "reasoning",
    "prompt_cache_options",
    "temperature",
    "top_p",
    "top_logprobs",
    "text_verbosity",
  ]);
  const originalFailures = failedRules(result);
  for (const rule of ["echo_service_tier", "echo_schema", "model"]) {
    assert.equal(originalFailures.includes(rule), true, `${rule} must remain visible`);
  }
  assert.equal(result.requestCount, 1);
  assert.equal(Object.hasOwn(result, "proposal"), false);
  assertPrivateDataAbsent(result);
});

const ORIGINAL_RULE_REJECTIONS = [
  {
    name: "usage arithmetic",
    diagnosticCode: "R22_PROVIDER_USAGE_INVALID",
    stage: "usage",
    rule: "usage",
    mutate(responseDocument) {
      responseDocument.usage.total_tokens += 1;
    },
  },
  {
    name: "model lock",
    diagnosticCode: "R22_PROVIDER_MODEL_MISMATCH",
    stage: "model",
    rule: "model",
    mutate(responseDocument) {
      responseDocument.model = "synthetic-model-drift";
    },
  },
  {
    name: "completion state",
    diagnosticCode: "R22_PROVIDER_RESPONSE_INVALID",
    stage: "completion",
    rule: "completion",
    mutate(responseDocument) {
      responseDocument.status = "incomplete";
    },
  },
  {
    name: "refusal content",
    diagnosticCode: "R22_PROVIDER_REFUSED",
    stage: "content",
    rule: "content",
    mutate(responseDocument) {
      responseDocument.output[0].content = [{ type: "refusal", refusal: privateEnvelopeText }];
    },
  },
  {
    name: "unknown action choice",
    diagnosticCode: "R22_ACTION_CHOICE_UNKNOWN",
    stage: "action_choice",
    rule: "action_choice",
    mutate(responseDocument) {
      const proposal = JSON.parse(responseDocument.output[0].content[0].text);
      proposal.actionChoiceId = forgedChoiceId;
      responseDocument.output[0].content[0].text = JSON.stringify(proposal);
    },
  },
];

const FIELD_POLICY_CONFLICTS = [
  {
    name: "invalid opaque id",
    failureField: "id",
    mutate(responseDocument) {
      responseDocument.id = 7;
    },
  },
  {
    name: "unverifiable billing object",
    failureField: "billing",
    mutate(responseDocument) {
      responseDocument.billing = { private_charge_data: privateEnvelopeText };
    },
  },
];

test("field-policy evidence never steals the original rule rejection or claims exact cost", async (t) => {
  for (const fieldCase of FIELD_POLICY_CONFLICTS) {
    for (const originalCase of ORIGINAL_RULE_REJECTIONS) {
      await t.test(`${fieldCase.name} with ${originalCase.name}`, async () => {
        const input = createSignedTurn();
        const responseDocument = createSyntheticResponse(input);
        fieldCase.mutate(responseDocument);
        originalCase.mutate(responseDocument);

        const result = await executeOffline(input, responseDocument);
        assert.equal(result.ok, false);
        assert.equal(result.diagnosticCode, originalCase.diagnosticCode);
        assert.equal(result.responseDiagnostic.stage, originalCase.stage);
        assert.equal(result.responseDiagnostic.envelopeProfile, "matrix-oasis.responses-envelope/1");
        assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, [fieldCase.failureField]);
        assert.equal(failedRules(result).includes(originalCase.rule), true,
          `${originalCase.rule} must remain visible`);
        assert.equal(result.requestCount, 1);
        assert.equal(result.costUncertain, true);
        assert.equal(result.usage, null);
        assert.equal(result.actualCostMicrousd, null);
        assert.equal(Object.hasOwn(result, "proposal"), false);
        assertPrivateDataAbsent(result);
      });
    }
  }
});

test("none reasoning effort permits metered internal tokens but never a reasoning output item", async () => {
  const acceptedInput = createSignedTurn();
  const acceptedResponse = createSyntheticResponse(acceptedInput);
  acceptedResponse.reasoning = { effort: "none", mode: "standard" };
  acceptedResponse.usage.output_tokens_details.reasoning_tokens = 1;
  const accepted = await executeOffline(acceptedInput, acceptedResponse);
  assertClosedProposal(accepted, expectedProposal(acceptedInput));
  assert.equal(accepted.usage.outputTokens, 10);
  assert.equal(accepted.actualCostMicrousd, 16);
  assertPrivateDataAbsent(accepted);

  const rejectedInput = createSignedTurn();
  const rejectedResponse = createSyntheticResponse(rejectedInput);
  rejectedResponse.reasoning = { effort: "none", mode: "standard" };
  rejectedResponse.usage.output_tokens_details.reasoning_tokens = 1;
  rejectedResponse.output.push({
    id: "reasoning_private_do_not_retain",
    type: "reasoning",
    summary: [],
  });
  const rejected = await executeOffline(rejectedInput, rejectedResponse);
  assert.equal(rejected.ok, false);
  assert.equal(rejected.diagnosticCode, "R22_UNTRUSTED_OUTPUT_REJECTED");
  assert.equal(rejected.responseDiagnostic.stage, "output");
  assert.equal(rejected.usage.outputTokens, 10);
  assert.equal(rejected.actualCostMicrousd, 16);
  assert.equal(Object.hasOwn(rejected, "proposal"), false);
  assertPrivateDataAbsent(rejected, ["reasoning_private_do_not_retain"]);
});

const ROOT_FIELD_POLICY_COVERAGE = Object.freeze({
  id: "bounded opaque metadata, then discarded",
  object: "completion gate",
  status: "completion gate",
  error: "completion gate",
  incomplete_details: "completion gate",
  model: "locked model and pricing gate",
  output: "single assistant message and closed proposal gate",
  usage: "strict token arithmetic and cost gate",
  agent: "disabled capability must be absent or null",
  background: "exact approved-request echo",
  billing: "absent/null or exact observed developer payer only; discarded, not cost or authority",
  completed_at: "bounded opaque metadata, then discarded",
  context_management: "disabled capability must be absent or null",
  conversation: "no previous conversation echo",
  created_at: "bounded opaque metadata, then discarded",
  frequency_penalty: "neutral zero-only echo",
  instructions: "exact approved-request echo",
  max_output_tokens: "exact approved-request echo",
  max_tool_calls: "disabled capability must be absent or null",
  metadata: "empty or null opaque metadata, then discarded",
  moderation: "disabled capability must be absent or null",
  parallel_tool_calls: "bounded boolean echo while tools stay empty",
  previous_response_id: "no previous response echo",
  presence_penalty: "neutral zero-only echo",
  prompt: "no prompt identity echo",
  prompt_cache_diagnostics: "disabled capability must be absent or null",
  prompt_cache_key: "disabled identity must be absent or null",
  prompt_cache_options: "closed implicit 30m local cache profile",
  prompt_cache_retention: "bounded local retention enum, then discarded",
  reasoning: "closed approved reasoning configuration echo",
  safety_identifier: "disabled identity must be absent or null",
  service_tier: "Standard tier and pricing gate",
  store: "exact approved-request echo",
  temperature: "bounded generation metadata, then discarded",
  text: "exact schema echo plus bounded verbosity metadata",
  tool_choice: "no-required-tool echo",
  tool_usage: "empty record or complete observed positive-zero counter profile only",
  tools: "empty tool list only",
  top_logprobs: "positive-zero-only generation metadata, then discarded",
  top_p: "bounded generation metadata, then discarded",
  truncation: "exact approved-request echo",
  user: "disabled identity must be absent or null",
});

test("every production response root field has a local type, capability, or explicit discard rationale", async () => {
  // Read two inert static arrays only. Never import, evaluate, or execute source
  // text: malformed or dynamic declarations fail this audit closed.
  // This completeness check cannot prove that a local restriction matches the
  // upstream contract; protocol compatibility has its own independent oracle.
  const source = await readFile(new URL("../src/index.mjs", import.meta.url), "utf8");
  const requiredFields = parseStaticStringArray(source, "RESPONSE_ROOT_REQUIRED_KEYS");
  const optionalFields = parseStaticStringArray(source, "RESPONSE_ROOT_OPTIONAL_KEYS");
  const productionFields = [...requiredFields, ...optionalFields];
  assert.equal(new Set(productionFields).size, productionFields.length,
    "required and optional response root fields must not overlap");
  assert.deepEqual(Object.keys(ROOT_FIELD_POLICY_COVERAGE).sort(), productionFields.sort());
  for (const rationale of Object.values(ROOT_FIELD_POLICY_COVERAGE)) {
    assert.equal(typeof rationale, "string");
    assert.match(rationale, /gate|echo|discarded|null|profile|only/u);
  }
});

// Only this closed counter shape comes from the redacted observation. The
// surrounding envelope, dialogue and token usage below remain independent fake
// data; this is not a replay of a retained official response or a billing proof.
function observedZeroToolUsage() {
  return {
    image_gen: {
      input_tokens: 0,
      input_tokens_details: { image_tokens: 0, text_tokens: 0 },
      output_tokens: 0,
      output_tokens_details: { image_tokens: 0, text_tokens: 0 },
      total_tokens: 0,
    },
    web_search: { num_requests: 0 },
  };
}

test("complete observed zero tool usage accepts the same closed proposal without changing request or token cost", async () => {
  const input = createSignedTurn();
  const base = createSyntheticResponse(input);
  const baseline = await executeOffline(input, base);
  assert.equal(baseline.ok, true);
  const document = { ...base, tool_usage: observedZeroToolUsage() };
  const before = JSON.stringify(document);
  const actual = await executeOffline(input, document);
  assert.equal(actual.ok, true, JSON.stringify(actual.responseDiagnostic));
  assert.deepEqual(actual, { ...baseline, responseBytes: Buffer.byteLength(before) });
  assert.equal(JSON.stringify(document), before);
  assert.equal(Object.hasOwn(JSON.parse(input.providerRequestJson), "tools"), false);
  assert.equal(Object.hasOwn(JSON.parse(input.providerRequestJson), "tool_choice"), false);
  assertPrivateDataAbsent(actual);
});

const observedCounterPaths = [
  ["tool_usage", "image_gen", "input_tokens"],
  ["tool_usage", "image_gen", "input_tokens_details", "image_tokens"],
  ["tool_usage", "image_gen", "input_tokens_details", "text_tokens"],
  ["tool_usage", "image_gen", "output_tokens"],
  ["tool_usage", "image_gen", "output_tokens_details", "image_tokens"],
  ["tool_usage", "image_gen", "output_tokens_details", "text_tokens"],
  ["tool_usage", "image_gen", "total_tokens"],
  ["tool_usage", "web_search", "num_requests"],
];
const observedObjectPaths = [
  ["tool_usage"], ["tool_usage", "image_gen"],
  ["tool_usage", "image_gen", "input_tokens_details"],
  ["tool_usage", "image_gen", "output_tokens_details"],
  ["tool_usage", "web_search"],
];
const atPath = (value, keys) => keys.reduce((node, key) => node[key], value);
function responseWithZeroTools(input) {
  return { ...createSyntheticResponse(input), tool_usage: observedZeroToolUsage() };
}
function rawNumberAt(document, keys, token) {
  const value = structuredClone(document);
  const marker = "R22_TEST_RAW_NUMBER_TOKEN";
  atPath(value, keys.slice(0, -1))[keys.at(-1)] = marker;
  const json = JSON.stringify(value);
  assert.equal(json.split(JSON.stringify(marker)).length, 2);
  // Preserve number spelling: JSON.stringify(-0) or a parsed underflow would
  // erase the exact attack this matrix must exercise.
  return json.replace(JSON.stringify(marker), token);
}
function assertUncertainRejection(result, { stage, rule, code } = {}) {
  assert.equal(result.ok, false);
  assert.equal(result.requestCount, 1);
  assert.equal(result.costUncertain, true);
  assert.equal(result.usage, null);
  assert.equal(result.actualCostMicrousd, null);
  assert.equal(Object.hasOwn(result, "proposal"), false);
  assert.equal(Object.hasOwn(result, "proposalJson"), false);
  if (stage) assert.equal(result.responseDiagnostic.stage, stage);
  if (rule) assert.ok(failedRules(result).includes(rule), JSON.stringify(result.responseDiagnostic));
  if (code) assert.equal(result.diagnosticCode, code);
  assertPrivateDataAbsent(result);
}

test("observed zero tool counters reject every missing, nonzero, typed or negative-zero leaf", async (t) => {
  const tokens = ["1", "-1", "0.5", '"0"', "true", "false", "null", "[]", "{}",
    "-0", "-0.0e0", "-0e-999", "1e-999", "-1e-999", "0.0001e-999", "1e999"];
  for (const keys of observedCounterPaths) await t.test(keys.join("."), async (child) => {
    const input = createSignedTurn();
    const document = responseWithZeroTools(input);
    const missing = structuredClone(document);
    delete atPath(missing, keys.slice(0, -1))[keys.at(-1)];
    assertUncertainRejection(await executeOffline(input, missing), { stage: "root_echo", rule: "echo_tool_usage" });
    for (const token of tokens) await child.test(token, async () => {
      const result = await executeOffline(input, null, { rawBody: rawNumberAt(document, keys, token) });
      assertUncertainRejection(result, { stage: "root_echo", rule: "echo_tool_usage" });
    });
  });
});

test("observed zero tool objects reject partial branches, extra keys and incorrect object types", async (t) => {
  for (const keys of observedObjectPaths) await t.test(keys.join("."), async () => {
    const input = createSignedTurn();
    const base = responseWithZeroTools(input);
    for (const key of Object.keys(atPath(base, keys))) {
      const changed = structuredClone(base);
      delete atPath(changed, keys)[key];
      assertUncertainRejection(await executeOffline(input, changed), { rule: "echo_tool_usage" });
    }
    for (const key of ["unknown_count", "__proto__", "constructor", "image_gen.extra"]) {
      const changed = structuredClone(base);
      Object.defineProperty(atPath(changed, keys), key, { value: 0, enumerable: true });
      assertUncertainRejection(await executeOffline(input, changed), { rule: "echo_tool_usage" });
    }
    for (const replacement of [null, [], 0, true, "0"]) {
      const changed = structuredClone(base);
      atPath(changed, keys.slice(0, -1))[keys.at(-1)] = replacement;
      assertUncertainRejection(await executeOffline(input, changed), { rule: "echo_tool_usage" });
    }
  });
});

test("observed zero tool profile is stable across twenty recursive key orderings and exact zero spellings", async () => {
  const input = createSignedTurn();
  const requestBefore = canonicalizeJsonValue(input);
  const baseline = await executeOffline(input, responseWithZeroTools(input));
  function reorder(value, offset) {
    if (Array.isArray(value)) return value.map((entry) => reorder(entry, offset + 1));
    if (value && typeof value === "object") {
      return rotateRecord(Object.fromEntries(Object.entries(value).map(([key, entry]) =>
        [key, reorder(entry, offset + 1)])), offset);
    }
    return value;
  }
  for (let index = 0; index < 20; index += 1) {
    const document = reorder(responseWithZeroTools(input), index);
    const result = await executeOffline(input, document);
    assert.deepEqual(result, baseline);
    assert.equal(canonicalizeJsonValue(input), requestBefore);
  }
  for (const keys of observedCounterPaths) for (const token of ["0.0", "0e0", "0E+99", "0e-999"]) {
    const rawBody = rawNumberAt(responseWithZeroTools(input), keys, token);
    const result = await executeOffline(input, null, { rawBody });
    assert.deepEqual(result, { ...baseline, responseBytes: Buffer.byteLength(rawBody) });
  }
  const escaped = JSON.stringify(responseWithZeroTools(input)).replace('"image_gen":', '"image\\u005fgen":');
  assert.deepEqual(await executeOffline(input, null, { rawBody: escaped }),
    { ...baseline, responseBytes: Buffer.byteLength(escaped) });
});

test("observed zero tool underflow guard checks numeric tokens, not strings, and does not change legacy profiles", async () => {
  const input = createSignedTurn();
  const base = responseWithZeroTools(input);
  const proposal = { ...expectedProposal(input), dialogueText: "The literal 1e-999 is only text.", actionChoiceId: null };
  base.output[0].content[0].text = JSON.stringify(proposal);
  assertClosedProposal(await executeOffline(input, base), proposal);
  for (const profile of ["absent", "empty", "observed"]) {
    const document = structuredClone(base);
    if (profile === "absent") delete document.tool_usage;
    if (profile === "empty") document.tool_usage = {};
    const result = await executeOffline(input, null, { rawBody: rawNumberAt(document, ["temperature"], "1e-999") });
    if (profile === "observed") assertUncertainRejection(result, { rule: "echo_tool_usage" });
    else assertClosedProposal(result, proposal);
  }
});

test("observed zero tools never bypass response gates and every failed new-shape response retains its full reservation", async (t) => {
  const changeProposal = (document, patch) => {
    const part = document.output[0].content[0];
    part.text = JSON.stringify({ ...JSON.parse(part.text), ...patch });
  };
  const cases = {
    "enabled tools": (doc) => { doc.tools = [{ type: "web_search" }]; },
    "required tool": (doc) => { doc.tool_choice = "required"; },
    "wrong instructions": (doc) => { doc.instructions = "Unapproved instructions."; },
    "wrong tier": (doc) => { doc.service_tier = "priority"; },
    "wrong model": (doc) => { doc.model = "different-model"; },
    "schema drift": (doc) => { doc.text.format.schema.additionalProperties = true; },
    "unknown billing": (doc) => { doc.billing = { amount: 0 }; },
    "unknown root": (doc) => { doc.unknown_capability = {}; },
    "invalid usage": (doc) => { doc.usage.total_tokens += 1; },
    "missing usage": (doc) => { doc.usage = null; },
    "invalid cached usage": (doc) => { doc.usage.input_tokens_details.cached_tokens = 21; },
    "invalid output tokens": (doc) => { doc.usage.output_tokens = 513; doc.usage.total_tokens = 533; },
    "excessive cost": (doc) => { doc.usage.input_tokens = 100000; doc.usage.total_tokens = 100010; },
    "incomplete": (doc) => { doc.status = "incomplete"; doc.incomplete_details = { reason: "max_output_tokens" }; },
    "two messages": (doc) => { doc.output.push(structuredClone(doc.output[0])); },
    "no message": (doc) => { doc.output = []; },
    "function call": (doc) => { doc.output = [{ type: "function_call", name: "untrusted", arguments: "{}" }]; },
    "web tool call": (doc) => { doc.output = [{ type: "web_search_call", id: "call_private", status: "completed" }]; },
    "image tool call": (doc) => { doc.output = [{ type: "image_generation_call", id: "call_private", result: "untrusted" }]; },
    "wrong role": (doc) => { doc.output[0].role = "user"; },
    "refusal": (doc) => { doc.output[0].content = [{ type: "refusal", refusal: privateEnvelopeText }]; },
    "extra content": (doc) => { doc.output[0].content.push(structuredClone(doc.output[0].content[0])); },
    "annotations": (doc) => { doc.output[0].content[0].annotations = [{ type: "url_citation", url: "untrusted-reference" }]; },
    "unknown content": (doc) => { doc.output[0].content[0].extra = privateEnvelopeText; },
    "broken proposal": (doc) => { doc.output[0].content[0].text = "{"; },
    "unknown proposal": (doc) => { changeProposal(doc, { extra: true }); },
    "wrong context": (doc) => { changeProposal(doc, { contextSha256: fixedDigest("f") }); },
    "empty dialogue": (doc) => { changeProposal(doc, { dialogueText: " " }); },
    "oversize dialogue": (doc) => { changeProposal(doc, { dialogueText: "x".repeat(2049) }); },
    "forged action": (doc) => { changeProposal(doc, { actionChoiceId: forgedChoiceId }); },
  };
  for (const [name, mutate] of Object.entries(cases)) await t.test(name, async () => {
    const input = createSignedTurn();
    const document = createSyntheticResponse(input);
    mutate(document);
    const before = await executeOffline(input, document);
    assert.equal(before.ok, false, name);
    const empty = await executeOffline(input, { ...document, tool_usage: {} });
    for (const key of ["diagnosticCode", "usage", "actualCostMicrousd", "costUncertain"]) {
      assert.deepEqual(empty[key], before[key], `legacy empty result: ${name}/${key}`);
    }
    const actual = await executeOffline(input, { ...document, tool_usage: observedZeroToolUsage() });
    assertUncertainRejection(actual, { stage: before.responseDiagnostic.stage, code: before.diagnosticCode });
    if (actual.responseDiagnostic.checks) {
      assert.equal(actual.responseDiagnostic.checks.find(({ rule }) => rule === "echo_tool_usage").status, "passed");
      // emptyRecord describes shape, not the acceptance rule.
      assert.equal(actual.responseDiagnostic.echoDetails.toolUsage.emptyRecord, "failed");
    }
  });
  const input = createSignedTurn();
  const outputFailure = createSyntheticResponse(input);
  outputFailure.output = [];
  for (const tools of [undefined, {}]) {
    const result = await executeOffline(input, tools === undefined ? outputFailure : { ...outputFailure, tool_usage: tools });
    assert.equal(result.costUncertain, false);
    assert.equal(result.actualCostMicrousd, 16);
    assert.equal(result.usage.totalTokens, 30);
  }
});

test("observed zero tools cannot bypass duplicate-key, UTF-8, JSON depth or byte limits", async () => {
  const input = createSignedTurn();
  const text = JSON.stringify(responseWithZeroTools(input));
  for (const duplicate of ['"input_tokens":0,"input_tokens":0', '"input_tokens":0,"input\\u005ftokens":0']) {
    const marker = '"image_gen":{"input_tokens":0';
    const rawBody = text.replace(marker, `"image_gen":{${duplicate}`);
    assert.notEqual(rawBody, text);
    assertUncertainRejection(await executeOffline(input, null, { rawBody }), { stage: "json" });
  }
  const excessiveDepth = text.replace('"num_requests":0', `"num_requests":${"[".repeat(257)}0${"]".repeat(257)}`);
  for (const rawBody of [`${text} false`, text.slice(0, -1), excessiveDepth]) {
    assertUncertainRejection(await executeOffline(input, null, { rawBody }), { stage: "json" });
  }
  assertUncertainRejection(await executeOffline(input, null, { rawBody: Buffer.concat([Buffer.from(text), Buffer.from([0xc3, 0x28])]) }));
  assertUncertainRejection(await executeOffline(input, null, { rawBody: text + " ".repeat(NPC_COGNITION_LIMITS.providerResponseBytes) }),
    { code: "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED" });
});

test("observed zero tool compatibility does not promote diagnostic observation into qualification or permit ordinary capture", async () => {
  const diagnosticInput = createNpcCognitionToolUsageDiagnosticPlan();
  const document = responseWithZeroTools(diagnosticInput);
  const proposal = { ...expectedProposal(diagnosticInput), actionChoiceId: null };
  document.output[0].content[0].text = JSON.stringify(proposal);
  document.tool_choice = "none";
  const body = JSON.parse(diagnosticInput.providerRequestJson);
  delete body.tools;
  delete body.tool_choice;
  const providerRequestJson = canonicalizeJsonValue(body);
  const plan = JSON.parse(diagnosticInput.callPlanJson);
  plan.providerPayloadSha256 = digest(providerRequestJson);
  plan.requestBytes = Buffer.byteLength(providerRequestJson);
  plan.approval.hash = computeNpcCognitionApprovalHash(plan);
  const ordinaryInput = { providerRequestJson, callPlanJson: canonicalizeJsonValue(plan), approvalHash: plan.approval.hash };
  for (const invalid of [false, true]) {
    if (invalid) document.tool_usage.web_search.extra = 0;
    const bytes = new TextEncoder().encode(JSON.stringify(document));
    const diagnostic = await evaluateNpcCognitionToolUsageDiagnosticFixture(diagnosticInput, bytes);
    const ordinary = await executeOffline(ordinaryInput, document);
    assert.deepEqual(diagnostic.providerResult, ordinary);
    assert.equal(diagnostic.fixtureOnly, true);
    assert.equal(diagnostic.realRequestCount, 0);
    assert.equal(diagnostic.qualificationEligible, false);
    assert.equal(diagnostic.observation.qualificationEligible, false);
    assert.equal(Object.hasOwn(ordinary, "observation"), false);
    if (invalid) assertUncertainRejection(ordinary, { rule: "echo_tool_usage" });
    else assertClosedProposal(ordinary, proposal);
  }
});

const FIELD_POLICY_DIAGNOSTIC_RULES = Object.freeze([
  "billing_absent_or_null", "reasoning_closed_record_or_null", "reasoning_effort_none",
  "reasoning_mode_standard", "reasoning_context_supported", "reasoning_summary_supported",
  "reasoning_generate_summary_supported",
]);

test("billing/reasoning diagnostics identify fixed subrules without retaining response values or names", async (t) => {
  const privateValue = "PRIVATE_BILLING_REASONING_VALUE";
  const cases = [
    ["unexplained billing object", (body) => { body.billing = { [privateValue]: privateValue }; }, "billing_absent_or_null", "object"],
    ["unexplained billing array", (body) => { body.billing = [privateValue]; }, "billing_absent_or_null", "array"],
    ["unexplained billing scalar", (body) => { body.billing = privateValue; }, "billing_absent_or_null", "string"],
    ["reasoning array", (body) => { body.reasoning = [privateValue]; }, "reasoning_closed_record_or_null", "array"],
    ["unknown reasoning key", (body) => { body.reasoning[privateValue] = privateValue; }, "reasoning_closed_record_or_null", "object"],
    ["unknown context", (body) => { body.reasoning.context = privateValue; }, "reasoning_context_supported", "string"],
    ["non-string context", (body) => { body.reasoning.context = 1; }, "reasoning_context_supported", "number"],
    ["unapproved effort", (body) => { body.reasoning.effort = privateValue; }, "reasoning_effort_none", "string"],
    ["unapproved mode", (body) => { body.reasoning.mode = privateValue; }, "reasoning_mode_standard", "string"],
    ["invalid summary", (body) => { body.reasoning.summary = privateValue; }, "reasoning_summary_supported", "string"],
    ["invalid legacy summary", (body) => { body.reasoning.generate_summary = privateValue; }, "reasoning_generate_summary_supported", "string"],
  ];
  for (const [name, mutate, failedRule, jsonType] of cases) await t.test(name, async () => {
    const input = createSignedTurn();
    const document = createSyntheticResponse(input);
    document.reasoning = { effort: "none", mode: "standard", context: "all_turns" };
    mutate(document);
    const result = await executeOffline(input, document);
    assertUncertainRejection(result, { stage: "envelope_profile" });
    assert.ok(result.responseDiagnostic.checks.every(({ status }) => status === "passed"));
    const details = result.responseDiagnostic.fieldPolicyChecks;
    assert.equal(Object.isFrozen(details), true);
    assert.deepEqual(details.map(({ rule }) => rule), FIELD_POLICY_DIAGNOSTIC_RULES);
    assert.deepEqual(details.filter(({ status }) => status === "failed"), [{ rule: failedRule, status: "failed", jsonType }]);
    for (const detail of details) {
      assert.deepEqual(Object.keys(detail), ["rule", "status", "jsonType"]);
      assert.equal(Object.isFrozen(detail), true);
    }
    if (failedRule === "reasoning_closed_record_or_null") {
      assert.ok(details.slice(2).every((detail) => detail.status === "not_checked" && detail.jsonType === "unavailable"));
    }
    assertPrivateDataAbsent(result, [privateValue]);
    assert.equal(Object.hasOwn(result, "proposal"), false);
  });
});

test("field diagnostics preserve failure precedence, conservative cost and 20-run canonical bytes", async () => {
  const input = createSignedTurn();
  const document = createSyntheticResponse(input);
  document.billing = { PRIVATE_BILLING_KEY: "PRIVATE_BILLING_VALUE" };
  document.reasoning = { effort: "none", mode: "standard", context: "PRIVATE_CONTEXT_VALUE" };
  const serialized = new Set();
  for (let index = 0; index < 20; index += 1) {
    const result = await executeOffline(input, document);
    assertUncertainRejection(result, { stage: "envelope_profile" });
    assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, ["billing", "reasoning"]);
    assert.deepEqual(result.responseDiagnostic.fieldPolicyChecks.filter(({ status }) => status === "failed").map(({ rule }) => rule),
      ["billing_absent_or_null", "reasoning_context_supported"]);
    assertPrivateDataAbsent(result, ["PRIVATE_BILLING_KEY", "PRIVATE_BILLING_VALUE", "PRIVATE_CONTEXT_VALUE"]);
    serialized.add(canonicalizeJsonValue(result));
  }
  assert.equal(serialized.size, 1);
  document.output = [];
  const outputFailure = await executeOffline(input, document);
  assertUncertainRejection(outputFailure, { stage: "output", code: "R22_UNTRUSTED_OUTPUT_REJECTED" });
  assert.equal(outputFailure.responseDiagnostic.fieldPolicyChecks.filter(({ status }) => status === "failed").length, 2);

  const success = await executeOffline(input, createSyntheticResponse(input));
  assertClosedProposal(success, expectedProposal(input));
  assert.equal(Object.hasOwn(success, "responseDiagnostic"), false);
  const unrelatedFailure = createSyntheticResponse(input);
  unrelatedFailure.output = [];
  const unrelated = await executeOffline(input, unrelatedFailure);
  assert.equal(Object.hasOwn(unrelated.responseDiagnostic, "fieldPolicyChecks"), false);
  assert.equal(unrelated.costUncertain, false);
  assert.equal(unrelated.actualCostMicrousd, 16);
});

// Hypotheses, NOT an upstream schema or a retained provider response. The exact
// payer-developer row was subsequently corroborated by the redacted 2026-09-19
// observation. All other objects remain invented probes, not new allowlists.
const BILLING_HYPOTHESES = [
  { id: "absent", present: false, jsonType: "absent" },
  { id: "null", value: null, jsonType: "null" },
  { id: "empty-record", value: {}, jsonType: "object" },
  { id: "payer-developer", value: { payer: "developer" }, jsonType: "object" },
  { id: "payer-user", value: { payer: "user" }, jsonType: "object" },
  { id: "payer-null", value: { payer: null }, jsonType: "object" },
  { id: "payer-unknown", value: { payer: "SYNTHETIC_UNKNOWN_PAYER" }, jsonType: "object" },
  { id: "payer-record", value: { payer: { role: "developer" } }, jsonType: "object" },
  { id: "amount-zero", value: { amount: 0, currency: "usd" }, jsonType: "object" },
  { id: "amount-nonzero", value: { amount: 100, currency: "usd" }, jsonType: "object" },
  { id: "tool-zero", value: { tool_costs: { total: 0 } }, jsonType: "object" },
  { id: "tool-nonzero", value: { tool_costs: { total: 100 } }, jsonType: "object" },
  { id: "tier-disagreement", value: { service_tier: "priority" }, jsonType: "object" },
  { id: "authority-bait", value: { approved: true, actionChoiceId: forgedChoiceId }, jsonType: "object" },
  { id: "private-nested", value: { SYNTHETIC_PRIVATE_BILLING_KEY: [
    { value: "SYNTHETIC_PRIVATE_BILLING_VALUE" },
  ] }, jsonType: "object" },
  { id: "empty-array", value: [], jsonType: "array" },
  { id: "array-record", value: [{ payer: "developer" }], jsonType: "array" },
  { id: "empty-string", value: "", jsonType: "string" },
  { id: "string", value: "SYNTHETIC_PRIVATE_BILLING_VALUE", jsonType: "string" },
  { id: "number-zero", value: 0, jsonType: "number" },
  { id: "number-nonzero", value: 100, jsonType: "number" },
  { id: "false", value: false, jsonType: "boolean" },
  { id: "true", value: true, jsonType: "boolean" },
];

function setHypotheticalBilling(document, row) {
  if (row.present === false) delete document.billing;
  else document.billing = structuredClone(row.value);
}

test("observed developer billing accepts only metadata without changing the approved request or token price", async () => {
  const input = createSignedTurn();
  const inputBefore = canonicalizeJsonValue(input);
  const baseline = await executeOffline(input, createSyntheticResponse(input));
  for (const toolProfile of ["absent", "empty", "observed-zero"]) {
    const document = createSyntheticResponse(input);
    document.billing = { payer: "developer" };
    if (toolProfile === "empty") document.tool_usage = {};
    if (toolProfile === "observed-zero") document.tool_usage = observedZeroToolUsage();
    const before = canonicalizeJsonValue(document);
    const result = await executeOffline(input, document);
    assertClosedProposal(result, expectedProposal(input));
    assert.deepEqual(result, { ...baseline, responseBytes: Buffer.byteLength(JSON.stringify(document)) },
      "only the measured response length changes, not cost or action authority");
    assert.equal(canonicalizeJsonValue(document), before);
    assert.equal(canonicalizeJsonValue(input), inputBefore);
    assert.equal(Object.hasOwn(result, "billing"), false);
  }
});

test("observed developer billing rejects type coercion, other payers and every extra charge or capability", async (t) => {
  const input = createSignedTurn();
  const values = [null, false, true, 0, [], {}, ["developer"], { value: "developer" },
    "", "user", "Developer", "DEVELOPER", " developer", "developer ", "developer\n",
    "develop\u0435r", "developer\u0000", "developer\u202e", "developer".repeat(300)];
  for (const [index, payer] of values.entries()) await t.test(`payer type or value ${index}`, async () => {
    const document = { ...createSyntheticResponse(input), billing: { payer } };
    const result = await executeOffline(input, document);
    assertUncertainRejection(result, { stage: "envelope_profile" });
    assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, ["billing"]);
    assertPrivateDataAbsent(result, [forgedChoiceId]);
  });
  const extras = [
    ["amount", 0], ["amount", null], ["amount", -1], ["amount", 0.01], ["currency", "usd"],
    ["service_tier", "default"], ["tool_costs", { total: 0 }], ["tools", []],
    ["approved", true], ["actionChoiceId", forgedChoiceId], ["__proto__", {}],
    ["constructor", null], ["toJSON", "untrusted"], ["SYNTHETIC_PRIVATE_BILLING_KEY", privateEnvelopeText],
  ];
  for (const [index, [key, value]] of extras.entries()) await t.test(`extra field ${index}`, async () => {
    const document = { ...createSyntheticResponse(input), billing: { payer: "developer", [key]: value } };
    const result = await executeOffline(input, document);
    assertUncertainRejection(result, { stage: "envelope_profile" });
    assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, ["billing"]);
    assertPrivateDataAbsent(result, ["SYNTHETIC_PRIVATE_BILLING_KEY", forgedChoiceId]);
  });
});

test("observed developer billing preserves every independent response gate and full reservation on failure", async (t) => {
  const changeProposal = (document, patch) => {
    const part = document.output[0].content[0];
    part.text = JSON.stringify({ ...JSON.parse(part.text), ...patch });
  };
  const attacks = {
    "tools": (doc) => { doc.tools = [{ type: "web_search" }]; },
    "tool choice": (doc) => { doc.tool_choice = "required"; },
    "counter charge": (doc) => { doc.tool_usage = { count: 0 }; },
    "instructions": (doc) => { doc.instructions = privateEnvelopeText; },
    "background": (doc) => { doc.background = true; },
    "store": (doc) => { doc.store = true; },
    "tier": (doc) => { doc.service_tier = "priority"; },
    "missing tier": (doc) => { delete doc.service_tier; },
    "model": (doc) => { doc.model = "different-model"; },
    "schema": (doc) => { doc.text.format.schema.additionalProperties = true; },
    "capability": (doc) => { doc.agent = {}; },
    "reasoning": (doc) => { doc.reasoning = { effort: "none", context: "unapproved" }; },
    "unknown root": (doc) => { doc.unknown_capability = {}; },
    "usage": (doc) => { doc.usage.total_tokens += 1; },
    "usage absent": (doc) => { delete doc.usage; },
    "usage null": (doc) => { doc.usage = null; },
    "cached usage": (doc) => { doc.usage.input_tokens_details.cached_tokens = 21; },
    "cache write usage": (doc) => { doc.usage.input_tokens_details.cache_write_tokens = 21; },
    "output limit": (doc) => { doc.usage.output_tokens = 513; doc.usage.total_tokens = 533; },
    "cost limit": (doc) => { doc.usage.input_tokens = 100000; doc.usage.total_tokens = 100010; },
    "incomplete": (doc) => { doc.status = "incomplete"; doc.incomplete_details = { reason: "max_output_tokens" }; },
    "no message": (doc) => { doc.output = []; },
    "multiple messages": (doc) => { doc.output.push(structuredClone(doc.output[0])); },
    "function output": (doc) => { doc.output = [{ type: "function_call", name: "untrusted", arguments: "{}" }]; },
    "tool output": (doc) => { doc.output = [{ type: "web_search_call", status: "completed", id: "untrusted" }]; },
    "role": (doc) => { doc.output[0].role = "developer"; },
    "refusal": (doc) => { doc.output[0].content = [{ type: "refusal", refusal: privateEnvelopeText }]; },
    "content count": (doc) => { doc.output[0].content.push(structuredClone(doc.output[0].content[0])); },
    "annotations": (doc) => { doc.output[0].content[0].annotations = [{ type: "untrusted" }]; },
    "malformed proposal": (doc) => { doc.output[0].content[0].text = "{"; },
    "proposal field": (doc) => { changeProposal(doc, { billing: { payer: "developer" } }); },
    "context": (doc) => { changeProposal(doc, { contextSha256: fixedDigest("f") }); },
    "empty dialogue": (doc) => { changeProposal(doc, { dialogueText: " " }); },
    "dialogue bytes": (doc) => { changeProposal(doc, { dialogueText: "x".repeat(2049) }); },
    "dialogue lines": (doc) => { changeProposal(doc, { dialogueText: "line\n".repeat(9) }); },
    "choice": (doc) => { changeProposal(doc, { actionChoiceId: forgedChoiceId }); },
  };
  const input = createSignedTurn();
  for (const toolProfile of ["absent", "empty", "observed-zero"]) {
    for (const [name, mutate] of Object.entries(attacks)) await t.test(`${toolProfile}/${name}`, async () => {
      const document = createSyntheticResponse(input);
      if (toolProfile === "empty") document.tool_usage = {};
      if (toolProfile === "observed-zero") document.tool_usage = observedZeroToolUsage();
      mutate(document);
      const control = await executeOffline(input, document);
      assert.equal(control.ok, false);
      document.billing = { payer: "developer" };
      const result = await executeOffline(input, document);
      assertUncertainRejection(result, { stage: control.responseDiagnostic.stage, code: control.diagnosticCode });
      assert.deepEqual(failedRules(result), failedRules(control));
      assert.equal(result.responseDiagnostic.fieldPolicyFailures?.includes("billing") ?? false, false);
      assertPrivateDataAbsent(result, [forgedChoiceId]);
    });
  }
  // Absence/null keep their old output-failure accounting, independent of the
  // new shape's conservative reservation. This is not a refund of old records.
  for (const value of [undefined, null]) {
    const document = createSyntheticResponse(input);
    document.output = [];
    if (value === null) document.billing = null;
    const result = await executeOffline(input, document);
    assert.equal(result.costUncertain, false);
    assert.equal(result.actualCostMicrousd, 16);
    assert.equal(result.usage.totalTokens, 30);
  }
});

test("observed developer billing cannot bypass wire parsing and remains stable for twenty key orderings", async () => {
  const input = createSignedTurn();
  const document = { ...createSyntheticResponse(input), billing: { payer: "developer" } };
  const baseline = await executeOffline(input, document);
  const text = JSON.stringify(document);
  const repeatedResults = new Set();
  for (let index = 0; index < 20; index += 1) {
    const rawBody = JSON.stringify(rotateRecord(document, index))
      .replace('"billing"', '"bi\\u006cling"').replace('"payer":"developer"', '"pa\\u0079er":"devel\\u006fper"');
    const result = await executeOffline(input, null, { rawBody });
    assert.deepEqual(result, { ...baseline, responseBytes: Buffer.byteLength(rawBody) });
    repeatedResults.add(canonicalizeJsonValue(result));
  }
  assert.equal(repeatedResults.size, 1);
  const corrupt = [
    text.replace('"payer":"developer"', '"payer":"user","payer":"developer"'),
    text.replace('"payer":"developer"', '"payer":"developer","pa\\u0079er":"developer"'),
    `${text.slice(0, -1)},"billing":{"payer":"developer"}}`,
    `${text.slice(0, -1)},"bi\\u006cling":{"payer":"developer"}}`,
    text.replace('"payer":"developer"', `"payer":${"[".repeat(257)}0${"]".repeat(257)}`),
    text.slice(0, -1), `${text} false`,
  ];
  for (const rawBody of corrupt) assertUncertainRejection(await executeOffline(input, null, { rawBody }), { stage: "json" });
  // JSON permits this escape lexically; the exact payer policy rejects the
  // decoded invalid string without retaining it. Do not relabel the parser.
  assertUncertainRejection(await executeOffline(input, null,
    { rawBody: text.replace('"payer":"developer"', '"payer":"\\ud800"') }), { stage: "envelope_profile" });
  assertUncertainRejection(await executeOffline(input, null,
    { rawBody: Buffer.concat([Buffer.from(text), Buffer.from([0xc3, 0x28])]) }));
  assertUncertainRejection(await executeOffline(input, null, { rawBody: text + " ".repeat(65536) }),
    { code: "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED" });
});

test("observed developer billing does not produce a false billing diagnostic for an independent reasoning failure", async () => {
  const input = createSignedTurn();
  const document = { ...createSyntheticResponse(input), billing: { payer: "developer" },
    reasoning: { effort: "none", context: "unapproved" } };
  const result = await executeOffline(input, document);
  assertUncertainRejection(result, { stage: "envelope_profile" });
  assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, ["reasoning"]);
  assert.deepEqual(result.responseDiagnostic.fieldPolicyChecks[0],
    { rule: "billing_absent_or_null", status: "not_checked", jsonType: "object" });
  assert.deepEqual(result.responseDiagnostic.fieldPolicyChecks.filter(({ status }) => status === "failed").map(({ rule }) => rule),
    ["reasoning_context_supported"]);
});

test("billing hypothesis matrix separates 276 local-policy cells from unknown upstream semantics", async (t) => {
  assert.equal(BILLING_HYPOTHESES.length, 23);
  assert.equal(new Set(BILLING_HYPOTHESES.map(({ id }) => id)).size, 23);
  const input = createSignedTurn();
  const inputBefore = canonicalizeJsonValue(input);
  const baseline = await executeOffline(input, createSyntheticResponse(input));
  assertClosedProposal(baseline, expectedProposal(input));
  let cells = 0;
  let successes = 0;
  let billingOnlyRejections = 0;
  for (const row of BILLING_HYPOTHESES) {
    for (const toolProfile of ["absent", "empty", "observed-zero"]) {
      for (const context of ["absent", "auto", "current_turn", "all_turns"]) {
        await t.test(`${row.id}/${toolProfile}/${context}`, async () => {
          const document = createSyntheticResponse(input);
          if (toolProfile === "empty") document.tool_usage = {};
          if (toolProfile === "observed-zero") document.tool_usage = observedZeroToolUsage();
          if (context !== "absent") document.reasoning = { effort: "none", mode: "standard", context };
          setHypotheticalBilling(document, row);
          const before = JSON.stringify(document);
          const result = await executeOffline(input, document);
          cells += 1;
          if (row.present === false || row.value === null || row.id === "payer-developer") {
            successes += 1;
            assertClosedProposal(result, expectedProposal(input));
            assert.deepEqual(result.usage, baseline.usage);
            assert.equal(result.actualCostMicrousd, 16);
            assert.equal(result.costUncertain, false);
          } else {
            billingOnlyRejections += 1;
            assertUncertainRejection(result, { stage: "envelope_profile", code: "R22_PROVIDER_RESPONSE_INVALID" });
            assert.equal(result.responseDiagnostic.checks.length, 28);
            assert.ok(result.responseDiagnostic.checks.every(({ status }) => status === "passed"));
            assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, ["billing"]);
            assert.deepEqual(result.responseDiagnostic.fieldPolicyChecks.filter(({ status }) => status === "failed"), [
              { rule: "billing_absent_or_null", status: "failed", jsonType: row.jsonType },
            ]);
            // A counterfactual on a separate FAKE document, not a production
            // repair. Success here identifies the local gate, not cost safety.
            const control = structuredClone(document);
            delete control.billing;
            const withoutBilling = await executeOffline(input, control);
            assertClosedProposal(withoutBilling, expectedProposal(input));
            assert.equal(withoutBilling.actualCostMicrousd, 16);
            assertPrivateDataAbsent(result, ["SYNTHETIC_PRIVATE_BILLING_KEY", "SYNTHETIC_PRIVATE_BILLING_VALUE",
              "SYNTHETIC_UNKNOWN_PAYER", forgedChoiceId]);
          }
          assert.equal(JSON.stringify(document), before, "the original response must remain intact");
          assert.equal(canonicalizeJsonValue(input), inputBefore, "the approved payload must remain identical");
        });
      }
    }
  }
  assert.deepEqual({ cells, successes, billingOnlyRejections }, { cells: 276, successes: 36, billingOnlyRejections: 240 });
});

test("billing hypothesis removal cannot rescue independent authority, usage, tier, schema or tool failures", async (t) => {
  const input = createSignedTurn();
  const attacks = [
    ...SAFETY_COUNTEREXAMPLES,
    { name: "nonzero observed tool counter", diagnosticCode: "R22_PROVIDER_RESPONSE_INVALID", stage: "root_echo",
      mutate(document) { document.tool_usage.web_search.num_requests = 1; } },
    { name: "unapproved reasoning mode", diagnosticCode: "R22_PROVIDER_RESPONSE_INVALID", stage: "envelope_profile",
      mutate(document) { document.reasoning = { effort: "none", mode: "pro", context: "all_turns" }; } },
  ];
  const shapes = BILLING_HYPOTHESES.filter(({ id }) => [
    "empty-record", "payer-developer", "amount-zero", "amount-nonzero", "authority-bait",
  ].includes(id));
  for (const attack of attacks) {
    const control = responseWithZeroTools(input);
    attack.mutate(control);
    const controlResult = await executeOffline(input, control);
    assertUncertainRejection(controlResult, { stage: attack.stage, code: attack.diagnosticCode });
    for (const row of shapes) await t.test(`${attack.name}/${row.id}`, async () => {
      const document = structuredClone(control);
      setHypotheticalBilling(document, row);
      const withBilling = await executeOffline(input, document);
      assertUncertainRejection(withBilling, { stage: attack.stage, code: attack.diagnosticCode });
      assert.deepEqual(failedRules(withBilling), failedRules(controlResult));
      const removed = structuredClone(document);
      delete removed.billing;
      assert.deepEqual(await executeOffline(input, removed), controlResult);
      assertPrivateDataAbsent(withBilling, [forgedChoiceId]);
    });
  }
});

test("billing hypothesis diagnostics cannot distinguish object semantics or prove that zero amounts are free", async () => {
  const input = createSignedTurn();
  const results = new Set();
  const diagnostics = new Set();
  for (const row of BILLING_HYPOTHESES.filter(({ jsonType, id }) => jsonType === "object" && id !== "payer-developer")) {
    const document = responseWithZeroTools(input);
    document.reasoning = { effort: "none", mode: "standard", context: "all_turns" };
    setHypotheticalBilling(document, row);
    for (let repeat = 0; repeat < 20; repeat += 1) {
      document.billing = rotateRecord(document.billing, repeat);
      const result = await executeOffline(input, document);
      assertUncertainRejection(result, { stage: "envelope_profile" });
      results.add(canonicalizeJsonValue(result));
      diagnostics.add(canonicalizeJsonValue(result.responseDiagnostic));
    }
  }
  assert.equal(diagnostics.size, 1, "shallow diagnostics carry no evidence of nested billing semantics");
  assert.equal(results.size, 1, "different hypothetical charges must not alter conservative rejection accounting");
});

test("billing hypothesis malformed wire probes fail before any proposal is exposed", async (t) => {
  const input = createSignedTurn();
  const base = JSON.stringify(responseWithZeroTools(input));
  const nested = `${'['.repeat(260)}0${']'.repeat(260)}`;
  const probes = [
    ["duplicate-root", `${base.slice(0, -1)},"billing":null,"billing":{}}`],
    ["escaped-duplicate-root", `${base.slice(0, -1)},"billing":null,"bi\\u006cling":{}}`],
    ["duplicate-nested", `${base.slice(0, -1)},"billing":{"payer":"developer","payer":"user"}}`],
    ["invalid-number", `${base.slice(0, -1)},"billing":{"amount":NaN}}`],
    ["depth-limit", `${base.slice(0, -1)},"billing":${nested}}`],
    ["response-byte-limit", `${base.slice(0, -1)},"billing":"${"x".repeat(NPC_COGNITION_LIMITS.providerResponseBytes)}"}`],
  ];
  for (const [name, rawBody] of probes) await t.test(name, async () => {
    const result = await executeOffline(input, null, { rawBody });
    assertUncertainRejection(result);
    assert.ok(["R22_PROVIDER_RESPONSE_INVALID", "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED"].includes(result.diagnosticCode));
  });
});

test("private billing observation cannot change production acceptance, proposal or conservative cost", async (t) => {
  const input = createSignedTurn();
  const inputBefore = canonicalizeJsonValue(input);
  // Synthetic correlation only. This is not a live capture approval.
  const observationBinding = { callPlanSha256: digest(input.callPlanJson), diagnosticApprovalSha256: fixedDigest("7") };
  for (const row of BILLING_HYPOTHESES) await t.test(row.id, async () => {
    const document = responseWithZeroTools(input);
    setHypotheticalBilling(document, row);
    const documentBefore = canonicalizeJsonValue(document);
    const before = await executeOffline(input, document);
    const observation = observeBilling(document.billing, observationBinding, { present: Object.hasOwn(document, "billing") });
    assert.equal(observation.status, "observed");
    assert.equal(observation.semanticCoverage, "observation_only");
    assert.equal(observation.qualificationEligible, false);
    assert.equal(Object.hasOwn(observation, "ok"), false);
    assert.equal(canonicalizeJsonValue(document), documentBefore);
    const after = await executeOffline(input, document);
    assert.equal(canonicalizeJsonValue(after), canonicalizeJsonValue(before));
    if (row.present === false || row.value === null || row.id === "payer-developer") {
      assertClosedProposal(after, expectedProposal(input));
      assert.equal(after.actualCostMicrousd, 16);
    } else {
      assertUncertainRejection(after, { stage: "envelope_profile" });
      assert.deepEqual(after.responseDiagnostic.fieldPolicyFailures, ["billing"]);
      // Provider reports unknown actual cost. The Host, not this observer or
      // parser, charges the full reservation (covered by the Host regression).
      assert.equal(after.actualCostMicrousd, null);
      assert.equal(after.costUncertain, true);
    }
    assert.equal(canonicalizeJsonValue(input), inputBefore);
  });
});
