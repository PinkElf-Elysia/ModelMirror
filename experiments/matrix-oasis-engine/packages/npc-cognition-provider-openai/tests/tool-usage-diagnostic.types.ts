import {
  createNpcCognitionToolUsageDiagnosticPlan,
  executeApprovedNpcCognitionTurn,
  type ApprovedNpcCognitionTurnInput,
  type OpenAiNpcCognitionProvider,
  type NpcCognitionToolUsageObservation,
  type NpcCognitionToolUsagePathObservation,
  createNpcCognitionBillingDiagnosticPlan,
  evaluateNpcCognitionBillingDiagnosticFixture,
  type NpcCognitionBillingPathObservation,
} from "@matrix-oasis/npc-cognition-provider-openai";

declare const provider: OpenAiNpcCognitionProvider;
declare const ordinary: ApprovedNpcCognitionTurnInput;
executeApprovedNpcCognitionTurn(ordinary, provider);
// @ts-expect-error A diagnostic plan is not an ordinary approved Turn.
executeApprovedNpcCognitionTurn(createNpcCognitionToolUsageDiagnosticPlan(), provider);

declare const failed: Extract<NpcCognitionToolUsageObservation, { status: "failed_limit" | "failed_internal" | "not_captured" }>;
// @ts-expect-error Failed observations must never carry partially collected paths.
const forgedFailure: NpcCognitionToolUsageObservation = { ...failed, paths: [] };
// @ts-expect-error A dynamic supplier path is not part of the fixed vocabulary.
const forgedPath: NpcCognitionToolUsagePathObservation = { path: "unknown.num_requests", counterStatus: "captured", jsonType: "number", value: 1 };
// @ts-expect-error Invalid counters cannot retain a value.
const forgedValue: NpcCognitionToolUsagePathObservation = { path: "web_search.num_requests", counterStatus: "invalid_number", jsonType: "number", value: 0 };
const validCounter: NpcCognitionToolUsagePathObservation = { path: "web_search.num_requests", counterStatus: "captured", jsonType: "number", value: 1 };
void [forgedFailure, forgedPath, forgedValue, validCounter];
// @ts-expect-error A billing diagnostic cannot enter the production Turn API.
executeApprovedNpcCognitionTurn(createNpcCognitionBillingDiagnosticPlan(), provider);
// @ts-expect-error The old profile cannot authorize billing capture.
evaluateNpcCognitionBillingDiagnosticFixture(createNpcCognitionToolUsageDiagnosticPlan(), new Uint8Array());
// @ts-expect-error Monetary values must never be retained in observations.
const billingValue: NpcCognitionBillingPathObservation = { path: "amount", jsonType: "number", category: "type_only", value: 0 };
// @ts-expect-error A payer literal cannot be reported for a currency path.
const billingCategory: NpcCognitionBillingPathObservation = { path: "currency", jsonType: "string", category: "literal_user" };
void [billingValue, billingCategory];
