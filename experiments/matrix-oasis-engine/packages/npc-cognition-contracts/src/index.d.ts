export type Sha256 = `sha256:${string}`;
export type NpcCognitionChoiceId = `choice-${string}`;

export interface NpcCognitionValidationDiagnostic {
  readonly phase: "parse" | "schema" | "semantic" | "integrity" | "canonical";
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface NpcCognitionValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly Readonly<NpcCognitionValidationDiagnostic>[];
}

export interface NpcCognitionIdentityBinding {
  readonly runtimePackSha256: Sha256;
  readonly runtimeReceiptSha256: Sha256;
  readonly authorityPolicySha256: Sha256;
  readonly behaviorPolicySha256: Sha256;
  readonly entityBindingSha256: Sha256;
  readonly derivedStateBundleSha256: Sha256;
}

export interface NpcCognitionLedgerPoint {
  readonly revision: number;
  readonly headSha256: Sha256 | null;
  readonly runtimeSnapshotSha256: Sha256;
}

export interface NpcCognitionPolicy {
  readonly format: "matrix-oasis.npc-cognition-policy";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly id: string;
  readonly contentVersion: string;
  readonly identities: Readonly<NpcCognitionIdentityBinding>;
  readonly actors: readonly Readonly<{
    actorEntityId: string;
    safeActions: readonly Readonly<{nodeId: string; actionId: string}>[];
  }>[];
  readonly limits: Readonly<{
    turnsPerTimeline: 64;
    turnsPerActor: 32;
    callsPerTimeline: 16;
    callsPerActor: 8;
    concurrentCalls: 1;
    candidateActionsPerTurn: 64;
    memoryEpisodesPerActor: 16;
    relationshipEdgesPerActor: 64;
    transientDialogueExchanges: 4;
    transientDialogueBytes: 8192;
    playerTextBytes: 4096;
    derivedContextBytes: 16384;
    providerRequestBytes: 32768;
    providerResponseBytes: 65536;
    dialogueBytes: 2048;
    dialogueLines: 8;
    maxOutputTokens: 512;
    timeoutMs: 30000;
    approvalLifetimeMs: 300000;
  }>;
  readonly budgets: Readonly<{
    perCallMicrousd: 10000;
    perTimelineMicrousd: 160000;
    perHostRunMicrousd: 1000000;
  }>;
}

export interface NpcCognitionTurnRequest {
  readonly format: "matrix-oasis.npc-cognition-turn-request";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly id: string;
  readonly timelineId: string;
  readonly actorEntityId: string;
  readonly sequence: number;
  readonly observed: Readonly<NpcCognitionLedgerPoint>;
  readonly derivedStateBundleSha256: Sha256;
  readonly playerText: string;
}

export interface NpcCognitionCallPlan {
  readonly format: "matrix-oasis.npc-cognition-call-plan";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly turnSha256: Sha256;
  readonly contextSha256: Sha256;
  readonly candidateSha256: Sha256;
  readonly providerPayloadSha256: Sha256;
  readonly responseSchemaSha256: Sha256;
  readonly endpoint: "https://api.openai.com/v1/responses";
  readonly model: "gpt-5.6-luna";
  readonly reasoningEffort: "none";
  readonly priceLock: Readonly<{
    inputMicrousdPerMillionTokens: 200000;
    cachedInputMicrousdPerMillionTokens: 20000;
    cacheWriteInputMicrousdPerMillionTokens: 250000;
    outputMicrousdPerMillionTokens: 1200000;
  }>;
  readonly maxOutputTokens: 512;
  readonly timeoutMs: 30000;
  readonly maxCostMicrousd: 10000;
  readonly requestBytes: number;
  readonly requestLimit: 1;
  readonly retryLimit: 0;
  readonly retentionPolicyVersion: "openai-api-data-controls-2026-09-03";
  readonly retention: Readonly<{
    store: false;
    zeroDataRetentionClaimed: false;
    abuseMonitoringMaxDays: 30;
    promptCachingPossible: true;
  }>;
  readonly approval: Readonly<{hash: Sha256; expiresAfterMs: 300000}>;
}

export interface NpcDialogueProposal {
  readonly format: "matrix-oasis.npc-dialogue-proposal";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly contextSha256: Sha256;
  readonly dialogueText: string;
  readonly actionChoiceId: NpcCognitionChoiceId | null;
}

export type NpcCognitionReceiptStatus =
  | "planned" | "approved" | "reserved" | "dispatching" | "validated"
  | "queued_for_r20" | "dialogue_only" | "fallback" | "adjudicated" | "finalized";

export type NpcCognitionFallbackReason =
  | "NPC_COGNITION_FALLBACK_NONE"
  | "NPC_COGNITION_FALLBACK_APPROVAL_DECLINED"
  | "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED"
  | "NPC_COGNITION_FALLBACK_BUDGET_EXHAUSTED"
  | "NPC_COGNITION_FALLBACK_PROVIDER_TIMEOUT"
  | "NPC_COGNITION_FALLBACK_PROVIDER_NETWORK_AMBIGUOUS"
  | "NPC_COGNITION_FALLBACK_DISPATCH_CRASH_UNCERTAIN"
  | "NPC_COGNITION_FALLBACK_PROVIDER_FAILURE"
  | "NPC_COGNITION_FALLBACK_PROVIDER_REFUSED"
  | "NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_INVALID"
  | "NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_LIMIT_EXCEEDED"
  | "NPC_COGNITION_FALLBACK_MODEL_MISMATCH"
  | "NPC_COGNITION_FALLBACK_USAGE_INVALID"
  | "NPC_COGNITION_FALLBACK_PROPOSAL_INVALID"
  | "NPC_COGNITION_FALLBACK_UNTRUSTED_OUTPUT_REJECTED"
  | "NPC_COGNITION_FALLBACK_CONTEXT_STALE"
  | "NPC_COGNITION_FALLBACK_CHOICE_INVALID"
  | "NPC_COGNITION_FALLBACK_ACTION_CHOICE_UNKNOWN"
  | "NPC_COGNITION_FALLBACK_PROVIDER_CREDENTIAL_UNAVAILABLE"
  | "NPC_COGNITION_FALLBACK_CALL_IN_FLIGHT"
  | "NPC_COGNITION_FALLBACK_R20_UNAVAILABLE"
  | "NPC_COGNITION_FALLBACK_R19_FAILURE";

export interface NpcCognitionTurnReceipt {
  readonly format: "matrix-oasis.npc-cognition-turn-receipt";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly turnSha256: Sha256;
  readonly callPlanSha256: Sha256;
  readonly approvalSha256: Sha256;
  readonly proposalSha256: Sha256 | null;
  readonly requestCount: 0 | 1;
  readonly status: "finalized";
  readonly statusHistory: readonly NpcCognitionReceiptStatus[];
  readonly requestedModel: "gpt-5.6-luna";
  readonly returnedModel: string | null;
  readonly usage: Readonly<{
    inputTokens: number;
    cachedInputTokens: number;
    cacheWriteInputTokens: number;
    outputTokens: number;
    totalTokens: number;
  }>;
  readonly budget: Readonly<{reservedMicrousd: number; actualMicrousd: number}>;
  readonly fallbackReason: NpcCognitionFallbackReason;
  readonly actionChoiceId: NpcCognitionChoiceId | null;
  readonly mappedIntentSha256: Sha256 | null;
  readonly adjudicationResultSha256: Sha256 | null;
  readonly ledger: Readonly<{before: Readonly<NpcCognitionLedgerPoint>; after: Readonly<NpcCognitionLedgerPoint>}>;
  readonly redaction: Readonly<{
    playerTextStored: false;
    contextStored: false;
    providerPayloadStored: false;
    dialogueTextStored: false;
    responseIdStored: false;
    credentialStored: false;
    rawErrorStored: false;
  }>;
}

export interface NpcCognitionTraceReceipt {
  readonly sequence: number;
  readonly actorEntityId: string;
  readonly turnSha256: Sha256;
  readonly callPlanSha256: Sha256;
  readonly turnReceiptSha256: Sha256;
  readonly requestCount: 0 | 1;
  readonly actualMicrousd: number;
  readonly outcome: "dialogue_only" | "fallback" | "adjudicated";
  readonly actionChoiceId: NpcCognitionChoiceId | null;
  readonly beforeLedger: Readonly<NpcCognitionLedgerPoint>;
  readonly afterLedger: Readonly<NpcCognitionLedgerPoint>;
  readonly interveningAuthorityEntrySha256: readonly Sha256[];
}

export interface NpcCognitionTrace {
  readonly format: "matrix-oasis.npc-cognition-trace";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly timelineId: string;
  readonly policySha256: Sha256;
  readonly initialLedger: Readonly<NpcCognitionLedgerPoint>;
  readonly receipts: readonly Readonly<NpcCognitionTraceReceipt>[];
  readonly totals: Readonly<{
    turns: number;
    providerRequests: number;
    actualMicrousd: number;
    fallbackTurns: number;
    actionChoiceTurns: number;
    adjudicatedTurns: number;
  }>;
  readonly throughRevision: number;
  readonly throughHeadSha256: Sha256 | null;
}

export interface NpcCognitionQualificationReport {
  readonly format: "matrix-oasis.npc-cognition-qualification-report";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly profile: "matrix-oasis.bounded-npc-cognition/1";
  readonly stage: "offline" | "final";
  readonly policySha256: Sha256;
  readonly traceSha256: Sha256;
  readonly worldEventLedgerReplayReportSha256: Sha256;
  readonly projectionQualificationReportSha256: Sha256;
  readonly offlineCases: readonly Readonly<{caseId: string; evidenceSha256: Sha256; traceSha256: Sha256}>[];
  readonly godotEvidenceSha256: Sha256;
  readonly performanceEvidenceSha256: Sha256;
  readonly providerCalls: Readonly<{fake: number; real: 0 | 1; replay: 0}>;
  readonly replay: Readonly<{
    modelOutputReproducible: false;
    dialogueContentRetained: false;
    providerReplayRequests: 0;
  }>;
  readonly markers: readonly [
    "R22_DIALOGUE_BUDGET_ENFORCED",
    "R22_FALLBACK_PLAYABLE",
    "R22_UNTRUSTED_OUTPUT_ADJUDICATED",
  ];
}

export declare const NPC_COGNITION_FORMAT_VERSION: "0.1.0";
export declare const NPC_COGNITION_CANONICALIZATION: "matrix-oasis.canonical-json/1";
export declare const NPC_COGNITION_POLICY_FORMAT: "matrix-oasis.npc-cognition-policy";
export declare const NPC_COGNITION_TURN_REQUEST_FORMAT: "matrix-oasis.npc-cognition-turn-request";
export declare const NPC_COGNITION_CALL_PLAN_FORMAT: "matrix-oasis.npc-cognition-call-plan";
export declare const NPC_DIALOGUE_PROPOSAL_FORMAT: "matrix-oasis.npc-dialogue-proposal";
export declare const NPC_COGNITION_TURN_RECEIPT_FORMAT: "matrix-oasis.npc-cognition-turn-receipt";
export declare const NPC_COGNITION_TRACE_FORMAT: "matrix-oasis.npc-cognition-trace";
export declare const NPC_COGNITION_QUALIFICATION_REPORT_FORMAT: "matrix-oasis.npc-cognition-qualification-report";
export declare const NPC_COGNITION_PROFILE: "matrix-oasis.bounded-npc-cognition/1";
export declare const NPC_COGNITION_ENDPOINT: "https://api.openai.com/v1/responses";
export declare const NPC_COGNITION_MODEL: "gpt-5.6-luna";
export declare const NPC_COGNITION_RETENTION_POLICY_VERSION: "openai-api-data-controls-2026-09-03";
export declare const NPC_COGNITION_LIMITS: Readonly<Record<string, number>>;
export declare const NPC_COGNITION_RECEIPT_STATUSES: readonly NpcCognitionReceiptStatus[];
export declare const NPC_COGNITION_FALLBACK_REASONS: readonly NpcCognitionFallbackReason[];
export declare const NPC_COGNITION_QUALIFICATION_MARKERS: readonly string[];
export declare const NPC_COGNITION_POLICY_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_COGNITION_TURN_REQUEST_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_COGNITION_CALL_PLAN_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_DIALOGUE_PROPOSAL_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_COGNITION_TURN_RECEIPT_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_COGNITION_TRACE_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_COGNITION_QUALIFICATION_REPORT_SCHEMA: Readonly<Record<string, unknown>>;

export declare class NpcCognitionContractOperationalError extends Error {
  readonly code: "NPC_COGNITION_CONTRACT_INTERNAL_ERROR";
}

export declare function computeNpcCognitionApprovalHash(callPlan: NpcCognitionCallPlan): Sha256;
export declare function validateNpcCognitionPolicyJson(text: string): NpcCognitionValidationReport;
export declare function validateNpcCognitionTurnRequestJson(text: string): NpcCognitionValidationReport;
export declare function validateNpcCognitionCallPlanJson(text: string): NpcCognitionValidationReport;
export declare function validateNpcDialogueProposalJson(text: string): NpcCognitionValidationReport;
export declare function validateNpcCognitionTurnReceiptJson(text: string): NpcCognitionValidationReport;
export declare function validateNpcCognitionTraceJson(text: string): NpcCognitionValidationReport;
export declare function validateNpcCognitionQualificationReportJson(text: string): NpcCognitionValidationReport;
