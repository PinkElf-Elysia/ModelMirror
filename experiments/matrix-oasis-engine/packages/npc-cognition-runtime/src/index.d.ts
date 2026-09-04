declare const preparedBrand: unique symbol;
declare const turnBrand: unique symbol;
declare const callPlanBrand: unique symbol;
declare const proposalBrand: unique symbol;
export interface PreparedNpcCognition { readonly [preparedBrand]: true }
export interface PreparedNpcCognitionTurn { readonly [turnBrand]: true }
export interface PreparedNpcCognitionCallPlan { readonly [callPlanBrand]: true }
export interface ValidatedNpcDialogueProposal { readonly [proposalBrand]: true }
export interface NpcCognitionFailure { readonly ok: false; readonly diagnostics: readonly Readonly<{ phase: string; severity: "error"; code: string; path: string; message: string }>[] }
export interface NpcCognitionTransientExchange { readonly sequence: number; readonly playerText: string; readonly dialogueText: string }
export interface NpcCognitionCurrentState { readonly runtimeSnapshot: unknown; readonly runtimeInspection: unknown; readonly worldEventLedgerJson: string; readonly behaviorState: unknown }

export declare class NpcCognitionRuntimeOperationalError extends Error { readonly code: "NPC_COGNITION_INTERNAL_ERROR" }
export declare const NPC_COGNITION_TRUSTED_INSTRUCTIONS_SHA256: `sha256:${string}`;

export declare function prepareNpcCognition(input: {
  readonly runtimeGamePackJson: string; readonly runtimeReceiptJson: string; readonly authorityPolicyJson: string;
  readonly behaviorPolicyJson: string; readonly npcEntityBindingJson: string; readonly personaSeedJson: string;
  readonly relationshipPolicyJson: string; readonly memoryProjectionJson: string; readonly relationshipProjectionJson: string;
  readonly memoryManifestJson: string; readonly relationshipManifestJson: string; readonly derivedStateBundleJson: string;
  readonly cognitionPolicyJson: string;
}): Promise<Readonly<{ ok: true; prepared: PreparedNpcCognition }> | NpcCognitionFailure>;

export declare function createNpcCognitionTurn(input: NpcCognitionCurrentState & {
  readonly prepared: PreparedNpcCognition; readonly timelineId: string; readonly actorEntityId: string;
  readonly sequence: number; readonly playerText: string; readonly transientDialogue?: readonly NpcCognitionTransientExchange[];
}): Readonly<{ ok: true; turn: PreparedNpcCognitionTurn; npcCognitionTurnRequest: unknown; canonicalNpcCognitionTurnRequestJson: string;
  contextSha256: `sha256:${string}`; contextBytes: number; pruning: Readonly<{ droppedMemoryEpisodes: number; droppedTransientDialogueExchanges: number }>;
  candidateActions: readonly unknown[] }> | NpcCognitionFailure;

export declare function planNpcCognitionCall(input: { readonly prepared: PreparedNpcCognition; readonly turn: PreparedNpcCognitionTurn }):
  Readonly<{ ok: true; callPlan: PreparedNpcCognitionCallPlan; npcCognitionCallPlan: unknown; canonicalNpcCognitionCallPlanJson: string;
    providerPayloadJson: string; responseSchemaJson: string; approvalHash: `sha256:${string}` }> | NpcCognitionFailure;

export declare function validateNpcDialogueProposal(input: NpcCognitionCurrentState & {
  readonly prepared: PreparedNpcCognition; readonly turn: PreparedNpcCognitionTurn; readonly callPlan: PreparedNpcCognitionCallPlan;
  readonly npcDialogueProposalJson: string;
}): Readonly<{ ok: true; validatedProposal: ValidatedNpcDialogueProposal; dialogueText: string; actionChoiceId: string | null; canonicalNpcDialogueProposalJson: string }> | NpcCognitionFailure;

export declare function mapNpcDialogueProposalToIntent(input: NpcCognitionCurrentState & {
  readonly prepared: PreparedNpcCognition; readonly turn: PreparedNpcCognitionTurn; readonly callPlan: PreparedNpcCognitionCallPlan;
  readonly validatedProposal: ValidatedNpcDialogueProposal;
}): Readonly<{ ok: true; status: "dialogue_only" | "queued_for_r20"; actionChoiceId: string | null; npcIntentJson: string | null; command: unknown; nextBehaviorState: unknown }> | NpcCognitionFailure;

export declare function replayNpcCognitionEvidence(input: {
  readonly prepared: PreparedNpcCognition; readonly worldEventLedgerJson: string; readonly cognitionTraceJson: string;
  readonly turnRecords: readonly Readonly<{ callPlanJson: string; turnReceiptJson: string }>[];
}): Readonly<{ ok: true; modelOutputReproducible: false; dialogueContentRetained: false; providerReplayRequests: 0;
  verifiedTurns: number; providerRequests: number; adjudicatedTurns: number; dialogueOnlyTurns: number; fallbackTurns: number;
  canonicalWorldEventLedgerReplayReportJson: string }> | NpcCognitionFailure;
