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
}

export interface NpcCognitionProviderUsage {
  readonly inputTokens: number;
  readonly cachedInputTokens: number;
  readonly cacheWriteInputTokens: number;
  readonly outputTokens: number;
  readonly totalTokens: number;
}

export interface NpcCognitionProviderFailure {
  readonly ok: false;
  readonly diagnosticCode: NpcCognitionProviderDiagnosticCode;
  readonly requestCount: 0 | 1;
  readonly costUncertain: boolean;
  readonly returnedModel: string | null;
  readonly usage: NpcCognitionProviderUsage | null;
  readonly actualCostMicrousd: number | null;
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
