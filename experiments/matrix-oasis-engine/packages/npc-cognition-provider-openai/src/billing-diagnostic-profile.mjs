import { createHash } from "node:crypto";
import { canonicalizeJsonValue as canonical } from "@matrix-oasis/runtime-pack-contracts";
import { computeNpcCognitionApprovalHash } from "@matrix-oasis/npc-cognition-contracts";
import { createNpcCognitionToolUsageDiagnosticPlan } from "./tool-usage-diagnostic-profile.mjs";
import { R22_BILLING_CAPTURE_POLICY, R22_BILLING_CAPTURE_POLICY_SHA256 } from "./billing-observer.mjs";

const hash = (text) => `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;

// Controlled comparison: the public request bytes stay fixed. Only the local
// observation/Turn/approval identity changes. This does not grant a new call slot.
export function createNpcCognitionBillingDiagnosticPlan() {
  const original = createNpcCognitionToolUsageDiagnosticPlan();
  const callPlan = JSON.parse(original.callPlanJson);
  const diagnosticProfile = R22_BILLING_CAPTURE_POLICY.profile;
  const capturePolicySha256 = R22_BILLING_CAPTURE_POLICY_SHA256;
  callPlan.turnId = "turn-billing-diagnostic";
  callPlan.turnSha256 = hash(`${diagnosticProfile}:public-synthetic-turn`);
  callPlan.approval.hash = computeNpcCognitionApprovalHash(callPlan);
  const callPlanJson = canonical(callPlan);
  const diagnosticApprovalSha256 = hash(canonical({ diagnosticProfile, capturePolicySha256,
    callPlanSha256: hash(callPlanJson), providerPayloadSha256: callPlan.providerPayloadSha256,
    approvalHash: callPlan.approval.hash }));
  return Object.freeze({ callPlanJson, providerRequestJson: original.providerRequestJson,
    approvalHash: callPlan.approval.hash, diagnosticProfile, capturePolicySha256, diagnosticApprovalSha256 });
}
