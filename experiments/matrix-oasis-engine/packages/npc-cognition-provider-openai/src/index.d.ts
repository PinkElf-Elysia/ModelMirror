export type NpcCognitionProviderDiagnosticCode =
  | "R22_APPROVAL_MISMATCH"
  | "R22_CALL_IN_FLIGHT"
  | "R22_PROVIDER_CREDENTIAL_UNAVAILABLE"
  | "R22_PROVIDER_TIMEOUT"
  | "R22_PROVIDER_NETWORK_AMBIGUOUS"
  | "R22_PROVIDER_REFUSED"
  | "R22_PROVIDER_RESPONSE_INVALID"
  | "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED"
  | "R22_PROVIDER_MODEL_MISMATCH"
  | "R22_PROVIDER_USAGE_INVALID"
  | "R22_UNTRUSTED_OUTPUT_REJECTED"
  | "R22_ACTION_CHOICE_UNKNOWN";

export interface OpenAiNpcCognitionProviderConfig {
  readonly apiKey: string;
  readonly fetchImplementation?: typeof fetch;
}

export interface OpenAiNpcCognitionProvider {
  readonly kind: "openai-npc-cognition";
  readonly endpoint: "https://api.openai.com/v1/responses";
  readonly model: "gpt-5.6-luna";
}

export interface ApprovedNpcCognitionTurnInput {
  readonly callPlanJson: string;
  readonly providerRequestJson: string;
  readonly approvalHash: string;
  readonly diagnosticProfile?: never;
  readonly capturePolicySha256?: never;
  readonly diagnosticApprovalSha256?: never;
}

export interface NpcCognitionProviderUsage {
  readonly inputTokens: number;
  readonly cachedInputTokens: number;
  readonly cacheWriteInputTokens: number;
  readonly outputTokens: number;
  readonly totalTokens: number;
}

export type NpcCognitionResponseCheckRule =
  | "echo_frequency_penalty" | "echo_presence_penalty" | "echo_tool_usage"
  | "echo_background" | "echo_store" | "echo_truncation" | "echo_max_output_tokens"
  | "echo_instructions" | "echo_previous_response_id" | "echo_conversation" | "echo_prompt"
  | "echo_tools" | "echo_tool_choice" | "echo_parallel_tool_calls" | "echo_metadata"
  | "echo_service_tier" | "echo_text_format" | "echo_schema" | "usage" | "model"
  | "completion" | "output" | "message" | "content" | "proposal_json"
  | "proposal_context" | "dialogue_text" | "action_choice";

export type NpcCognitionResponseObservationStatus = "passed" | "failed" | "not_checked";
export type NpcCognitionResponseObservedJsonType =
  | "absent" | "unavailable" | "null" | "array" | "object" | "string" | "number" | "boolean";

export type NpcCognitionResponseFieldPolicyRule =
  | "billing_absent_or_null" | "reasoning_closed_record_or_null"
  | "reasoning_effort_none" | "reasoning_mode_standard" | "reasoning_context_supported"
  | "reasoning_summary_supported" | "reasoning_generate_summary_supported";

export interface NpcCognitionResponseEchoDetails {
  readonly toolUsage: Readonly<{
    readonly jsonType: NpcCognitionResponseObservedJsonType;
    readonly objectRecord: NpcCognitionResponseObservationStatus;
    readonly emptyRecord: NpcCognitionResponseObservationStatus;
  }>;
  readonly textFormat: Readonly<{
    readonly textJsonType: NpcCognitionResponseObservedJsonType;
    readonly formatJsonType: NpcCognitionResponseObservedJsonType;
    readonly descriptionJsonType: NpcCognitionResponseObservedJsonType;
    readonly textRecord: NpcCognitionResponseObservationStatus;
    readonly textRequiredKeys: NpcCognitionResponseObservationStatus;
    readonly textAllowedKeys: NpcCognitionResponseObservationStatus;
    readonly formatRecord: NpcCognitionResponseObservationStatus;
    readonly formatRequiredKeys: NpcCognitionResponseObservationStatus;
    readonly formatAllowedKeys: NpcCognitionResponseObservationStatus;
    readonly typeJsonSchema: NpcCognitionResponseObservationStatus;
    readonly nameExact: NpcCognitionResponseObservationStatus;
    readonly strictTrue: NpcCognitionResponseObservationStatus;
    readonly descriptionString: NpcCognitionResponseObservationStatus;
    readonly descriptionSafeText: NpcCognitionResponseObservationStatus;
    readonly descriptionUtf8Limit: NpcCognitionResponseObservationStatus;
  }>;
}

export interface NpcCognitionProviderFailure {
  readonly ok: false;
  readonly diagnosticCode: NpcCognitionProviderDiagnosticCode;
  readonly requestCount: 0 | 1;
  readonly costUncertain: boolean;
  readonly returnedModel: string | null;
  readonly usage: NpcCognitionProviderUsage | null;
  readonly actualCostMicrousd: number | null;
  readonly responseDiagnostic?: Readonly<{
    readonly status: 200;
    readonly stage: "content_type" | "body" | "json" | "root_fields" | "root_echo" | "text_format" | "schema_echo" | "envelope_profile"
      | "usage" | "model" | "completion" | "output" | "message" | "content"
      | "proposal_json" | "proposal_context" | "dialogue_text" | "action_choice";
    /** Only after strict root capture. Exactly 28 fixed rules, in stable order.
     * Independent checks continue after a rejection; dependent checks may be
     * not_checked. Observation only: first rejection and accounting remain authoritative. */
    readonly checks?: readonly Readonly<{
      readonly rule: NpcCognitionResponseCheckRule;
      readonly status: "passed" | "failed" | "not_checked";
    }>[];
    /** Local response profile; not a claim of the full upstream API schema. */
    readonly envelopeProfile?: "matrix-oasis.responses-envelope/1";
    /** Fixed failed policy fields only. No response values or nested names. */
    readonly fieldPolicyFailures?: readonly (
      "id" | "created_at" | "completed_at" | "agent" | "billing" | "context_management"
      | "moderation" | "prompt_cache_diagnostics" | "max_tool_calls" | "prompt_cache_key"
      | "safety_identifier" | "user" | "reasoning" | "prompt_cache_options" | "prompt_cache_retention"
      | "temperature" | "top_p" | "top_logprobs" | "text_verbosity"
    )[];
    /** Failure-only seven fixed rules for billing/reasoning. Values and dynamic
     * field names are never retained. Diagnostic only, not acceptance evidence. */
    readonly fieldPolicyChecks?: readonly Readonly<{
      readonly rule: NpcCognitionResponseFieldPolicyRule;
      readonly status: NpcCognitionResponseObservationStatus;
      readonly jsonType: NpcCognitionResponseObservedJsonType;
    }>[];
    /** Failure-only fixed-key observations; no values, provider key names or sizes.
     * absent means a missing optional field; unavailable means its parent did not
     * pass capture. Dependent rules stay not_checked. Not an acceptance input. */
    readonly echoDetails?: Readonly<NpcCognitionResponseEchoDetails>;
    /** Present only for root_fields; an observation, never acceptance evidence. */
    readonly rootFields?: Readonly<{
      readonly rootKind: "record";
      /** 0..255, bits: id, object, status, error, incomplete_details, model, output, usage. */
      readonly missingRequiredMask: number;
      readonly unknownKeysPresent: boolean;
      /** Only when unknown keys exist: at most 16 ASCII names, each at most 64 characters. */
      readonly unknownFields?: readonly Readonly<{
        readonly name: string;
        readonly jsonType: "null" | "array" | "object" | "string" | "number" | "boolean";
      }>[];
      /** Unsafe names or entries beyond the cap were omitted; no omitted count or value. */
      readonly unknownFieldsOmitted?: boolean;
    } | {
      readonly rootKind: "array" | "non_object";
      readonly missingRequiredMask: null;
      readonly unknownKeysPresent: null;
    }>;
  }>;
  readonly httpDiagnostic?: Readonly<{
    readonly status: number;
    readonly bodyStatus: "parsed" | "invalid" | "timeout" | "limit_exceeded" | "unavailable";
    readonly errorType: "invalid_request_error" | "authentication_error" | "permission_denied_error"
      | "insufficient_quota" | "rate_limit_error" | "server_error" | "service_unavailable_error" | null;
    readonly errorCode: "invalid_json_schema" | "invalid_value" | "unsupported_value" | "unsupported_parameter"
      | "missing_required_parameter" | "invalid_api_key" | "model_not_found" | "insufficient_quota"
      | "rate_limit_exceeded" | "slow_down" | "organization_spend_limit_exceeded"
      | "project_spend_limit_exceeded" | "organization_usage_limit_exceeded" | "server_is_overloaded"
      | "context_length_exceeded" | "content_policy_violation" | null;
    readonly parameter: "model" | "input" | "instructions" | "reasoning" | "reasoning.effort" | "max_output_tokens"
      | "text" | "text.format" | "text.format.schema" | "text.format.name" | "text.format.strict"
      | "store" | "stream" | "background" | "truncation" | "service_tier" | null;
  }>;
}

export interface NpcCognitionProviderSuccess {
  readonly ok: true;
  readonly requestCount: 1;
  readonly costUncertain: false;
  readonly returnedModel: "gpt-5.6-luna";
  readonly usage: NpcCognitionProviderUsage;
  readonly actualCostMicrousd: number;
  readonly responseBytes: number;
  readonly proposal: Readonly<{
    contextSha256: string;
    dialogueText: string;
    actionChoiceId: string | null;
  }>;
  readonly proposalJson: string;
}

export type NpcCognitionProviderResult =
  | NpcCognitionProviderSuccess
  | NpcCognitionProviderFailure;

export declare class NpcCognitionProviderOperationalError extends Error {
  readonly code: "NPC_COGNITION_INTERNAL_ERROR";
}

export declare function createOpenAiNpcCognitionProvider(
  config: OpenAiNpcCognitionProviderConfig,
): OpenAiNpcCognitionProvider;

export declare function executeApprovedNpcCognitionTurn(
  input: ApprovedNpcCognitionTurnInput,
  provider: OpenAiNpcCognitionProvider,
): Promise<NpcCognitionProviderResult>;

/** Pure local planning only. A hash is NOT proof of human approval or dispatch.
 * Public package APIs expose no live diagnostic sender; the guarded CLI owns it. */
export interface NpcCognitionToolUsageDiagnosticPlan {
  readonly callPlanJson: string;
  readonly providerRequestJson: string;
  readonly approvalHash: string;
  readonly diagnosticProfile: "matrix-oasis.r22-tool-usage-diagnostic/1";
  readonly capturePolicySha256: string;
  readonly diagnosticApprovalSha256: string;
}

interface NpcCognitionToolUsageObservationIdentity {
  readonly profile: "matrix-oasis.r22-tool-usage-diagnostic/1";
  readonly capturePolicySha256: string;
  readonly semanticCoverage: "observation_only";
  readonly qualificationEligible: false;
  readonly binding: Readonly<{ callPlanSha256: string; diagnosticApprovalSha256: string }>;
}

export type NpcCognitionToolUsagePath =
  | "image_gen.input_tokens" | "image_gen.input_tokens_details.image_tokens" | "image_gen.input_tokens_details.text_tokens"
  | "image_gen.output_tokens" | "image_gen.output_tokens_details.image_tokens" | "image_gen.output_tokens_details.text_tokens"
  | "image_gen.total_tokens" | "web_search.num_requests";

export type NpcCognitionToolUsagePathObservation = Readonly<{ path: NpcCognitionToolUsagePath }> & (
  | Readonly<{ counterStatus: "captured"; jsonType: "number"; value: number }>
  | Readonly<{ counterStatus: "absent"; jsonType: "absent"; value?: never }>
  | Readonly<{ counterStatus: "not_captured"; jsonType: "not-captured"; value?: never }>
  | Readonly<{ counterStatus: "invalid_number"; jsonType: "number"; value?: never }>
  | Readonly<{ counterStatus: "not_counter"; jsonType: "null" | "object" | "array" | "string" | "boolean"; value?: never }>
);

export type NpcCognitionToolUsageObservation = NpcCognitionToolUsageObservationIdentity & (
  | Readonly<{
    status: "observed";
    rootType: "absent" | "null" | "object" | "array" | "string" | "number" | "boolean";
    coverage: "vocabulary_only" | "redacted";
    paths: readonly NpcCognitionToolUsagePathObservation[];
    summary: Readonly<{
      nodeCount: number; maxDepth: number; unknownFieldCount: number;
      typeCounts: Readonly<Record<"null" | "object" | "array" | "string" | "number" | "boolean", number>>;
    }>;
  }>
  | Readonly<{ status: "failed_limit" | "failed_internal" | "not_captured";
    rootType?: never; coverage?: never; paths?: never; summary?: never }>
);

export declare function createNpcCognitionToolUsageDiagnosticPlan(): Readonly<NpcCognitionToolUsageDiagnosticPlan>;

/** Only evaluates supplied synthetic bytes with an internal synthetic transport. Does not
 * accept/read credentials or use the network; results are not dispatch evidence.
 * providerResult is transient, and is NOT the redacted observation to persist. */
export declare function evaluateNpcCognitionToolUsageDiagnosticFixture(
  input: NpcCognitionToolUsageDiagnosticPlan,
  responseBytes: Uint8Array,
): Promise<Readonly<{
  fixtureOnly: true;
  realRequestCount: 0;
  qualificationEligible: false;
  providerResult: NpcCognitionProviderResult;
  observation: Readonly<NpcCognitionToolUsageObservation> | null;
}>>;

/** Local observation plan only; neither a call authorization nor a new budget. */
export interface NpcCognitionBillingDiagnosticPlan extends Omit<NpcCognitionToolUsageDiagnosticPlan, "diagnosticProfile"> {
  readonly diagnosticProfile: "matrix-oasis.r22-billing-observation/1";
}
export type NpcCognitionBillingPath = "payer" | "amount" | "currency" | "service_tier" | "tool_costs" | "tool_costs.total";
export type NpcCognitionBillingPathObservation = Readonly<{ path: NpcCognitionBillingPath }> & (
  | Readonly<{ jsonType: "absent"; category: "absent" }>
  | Readonly<{ jsonType: "not-captured"; category: "not_captured" }>
  | Readonly<{ jsonType: "null" | "object" | "array" | "number" | "boolean"; category: "type_only" }>
  | Readonly<{ jsonType: "string"; category: "other_string" }>
  | Readonly<{ path: "payer"; jsonType: "string"; category: "literal_developer" | "literal_user" }>
  | Readonly<{ path: "currency"; jsonType: "string"; category: "literal_usd" }>
  | Readonly<{ path: "service_tier"; jsonType: "string"; category: "literal_default" }>
);
export type NpcCognitionBillingObservation = Omit<NpcCognitionToolUsageObservationIdentity, "profile"> &
  Readonly<{ profile: "matrix-oasis.r22-billing-observation/1" }> & (
  | Readonly<{ status: "observed"; rootType: "absent" | "null" | "object" | "array" | "string" | "number" | "boolean";
    paths: readonly NpcCognitionBillingPathObservation[];
    summary: Readonly<{ nodeCount: number; maxDepth: number; unknownFieldCount: number;
      typeCounts: Readonly<Record<"null" | "object" | "array" | "string" | "number" | "boolean", number>> }> }>
  | Readonly<{ status: "failed_limit" | "failed_internal" | "not_captured"; rootType?: never; paths?: never; summary?: never }>
);
export declare function createNpcCognitionBillingDiagnosticPlan(): Readonly<NpcCognitionBillingDiagnosticPlan>;
/** Supplied synthetic bytes only. No credential or transport callback accepted.
 * The observation cannot establish fee semantics or change response acceptance. */
export declare function evaluateNpcCognitionBillingDiagnosticFixture(
  input: NpcCognitionBillingDiagnosticPlan, responseBytes: Uint8Array,
): Promise<Readonly<{ fixtureOnly: true; realRequestCount: 0; qualificationEligible: false;
  providerResult: NpcCognitionProviderResult; observation: Readonly<NpcCognitionBillingObservation> | null }>>;
