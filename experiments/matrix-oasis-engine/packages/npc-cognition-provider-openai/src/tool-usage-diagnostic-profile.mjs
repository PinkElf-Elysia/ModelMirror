import { createHash } from "node:crypto";
import {
  NPC_COGNITION_CANONICALIZATION, NPC_COGNITION_CALL_PLAN_FORMAT, NPC_COGNITION_FORMAT_VERSION,
  NPC_COGNITION_ENDPOINT, NPC_COGNITION_MODEL, NPC_COGNITION_LIMITS,
  NPC_COGNITION_RETENTION_POLICY_VERSION, NPC_COGNITION_TRUSTED_INSTRUCTIONS,
  computeNpcCognitionApprovalHash,
} from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { R22_TOOL_USAGE_CAPTURE_POLICY, R22_TOOL_USAGE_CAPTURE_POLICY_SHA256 } from "./tool-usage-observer.mjs";

const hash = (text) => `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;

// Local planning is not approval, dispatch, or a new spending account. No source
// game, user text, private identities, credentials, clock, or network is consulted.
export function createNpcCognitionToolUsageDiagnosticPlan() {
  const input = canonicalizeJsonValue({
    diagnostic: "Public synthetic response-format observation; no game or player data.",
    playerText: "Please give a short neutral acknowledgement without taking any action.",
    candidates: [],
  });
  const contextSha256 = hash(input);
  const schema = {
    type: "object", additionalProperties: false,
    required: ["contextSha256", "dialogueText", "actionChoiceId"],
    properties: {
      contextSha256: { type: "string", const: contextSha256 },
      dialogueText: { type: "string", minLength: 1, maxLength: NPC_COGNITION_LIMITS.dialogueBytes },
      actionChoiceId: { type: ["string", "null"], enum: [null] },
    },
  };
  const providerRequestJson = canonicalizeJsonValue({
    background: false, input, instructions: NPC_COGNITION_TRUSTED_INSTRUCTIONS,
    max_output_tokens: NPC_COGNITION_LIMITS.maxOutputTokens, model: NPC_COGNITION_MODEL,
    reasoning: { effort: "none" }, service_tier: "default", store: false, stream: false,
    text: { format: { type: "json_schema", name: "matrix_oasis_npc_dialogue_proposal", strict: true, schema } },
    tools: [], tool_choice: "none", truncation: "disabled",
  });
  const callPlan = {
    format: NPC_COGNITION_CALL_PLAN_FORMAT, formatVersion: NPC_COGNITION_FORMAT_VERSION,
    canonicalization: NPC_COGNITION_CANONICALIZATION, turnId: "turn-tool-usage-diagnostic",
    turnSha256: hash("matrix-oasis.r22-tool-usage-diagnostic/1:public-synthetic-turn"),
    contextSha256, candidateSha256: hash("[]"), candidateChoices: [],
    providerPayloadSha256: hash(providerRequestJson), responseSchemaSha256: hash(canonicalizeJsonValue(schema)),
    endpoint: NPC_COGNITION_ENDPOINT, model: NPC_COGNITION_MODEL, reasoningEffort: "none",
    priceLock: {
      inputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.inputMicrousdPerMillionTokens,
      cachedInputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.cachedInputMicrousdPerMillionTokens,
      cacheWriteInputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.cacheWriteInputMicrousdPerMillionTokens,
      outputMicrousdPerMillionTokens: NPC_COGNITION_LIMITS.outputMicrousdPerMillionTokens,
    },
    maxOutputTokens: NPC_COGNITION_LIMITS.maxOutputTokens, timeoutMs: NPC_COGNITION_LIMITS.timeoutMs,
    maxCostMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
    requestBytes: Buffer.byteLength(providerRequestJson), requestLimit: 1, retryLimit: 0,
    retentionPolicyVersion: NPC_COGNITION_RETENTION_POLICY_VERSION,
    retention: { store: false, zeroDataRetentionClaimed: false, abuseMonitoringMaxDays: 30, promptCachingPossible: true },
    approval: { hash: `sha256:${"0".repeat(64)}`, expiresAfterMs: NPC_COGNITION_LIMITS.approvalLifetimeMs },
  };
  callPlan.approval.hash = computeNpcCognitionApprovalHash(callPlan);
  const callPlanJson = canonicalizeJsonValue(callPlan);
  const diagnosticProfile = R22_TOOL_USAGE_CAPTURE_POLICY.profile;
  const capturePolicySha256 = R22_TOOL_USAGE_CAPTURE_POLICY_SHA256;
  const diagnosticApprovalSha256 = hash(canonicalizeJsonValue({
    diagnosticProfile, capturePolicySha256, callPlanSha256: hash(callPlanJson),
    providerPayloadSha256: callPlan.providerPayloadSha256, approvalHash: callPlan.approval.hash,
  }));
  return Object.freeze({ callPlanJson, providerRequestJson, approvalHash: callPlan.approval.hash,
    diagnosticProfile, capturePolicySha256, diagnosticApprovalSha256 });
}
