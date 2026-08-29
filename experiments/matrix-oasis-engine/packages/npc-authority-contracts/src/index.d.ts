export type NpcAuthoritySha256 = `sha256:${string}`;
export type NpcAuthorityDiagnosticPhase = "parse" | "schema" | "semantic" | "integrity" | "canonical";

export interface NpcAuthorityDiagnostic {
  readonly phase: NpcAuthorityDiagnosticPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface NpcAuthorityValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly NpcAuthorityDiagnostic[];
}

export interface NpcRuntimeIdentity {
  readonly format: "matrix-oasis.runtime-game-pack";
  readonly formatVersion: "0.1.0";
  readonly id: string;
  readonly contentVersion: string;
  readonly sourceSha256: NpcAuthoritySha256;
  readonly artifactSha256: NpcAuthoritySha256;
  readonly receiptSha256: NpcAuthoritySha256;
}

export interface NpcAuthorityGrant {
  readonly nodeId: string;
  readonly actionId: string;
}

export interface NpcActorGrant {
  readonly actorEntityId: string;
  readonly grants: readonly NpcAuthorityGrant[];
}

export interface NpcAuthorityPolicy {
  readonly format: "matrix-oasis.npc-authority-policy";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly id: string;
  readonly contentVersion: string;
  readonly runtime: NpcRuntimeIdentity;
  readonly actorGrants: readonly NpcActorGrant[];
}

export interface NpcIntentObservedState {
  readonly revision: number;
  readonly headSha256: NpcAuthoritySha256 | null;
  readonly runtimeSnapshotSha256: NpcAuthoritySha256;
}

export interface NpcIntent {
  readonly format: "matrix-oasis.npc-intent";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly id: string;
  readonly actorEntityId: string;
  readonly timelineId: string;
  readonly nodeId: string;
  readonly actionId: string;
  readonly observed: NpcIntentObservedState;
}

export type NpcIntentRejectionReason =
  | "NPC_INTENT_ACTOR_NOT_FOUND"
  | "NPC_INTENT_ACTOR_UNAUTHORIZED"
  | "NPC_INTENT_NODE_NOT_FOUND"
  | "NPC_INTENT_ACTION_NOT_FOUND"
  | "NPC_INTENT_NODE_MISMATCH"
  | "NPC_INTENT_ACTION_UNAVAILABLE"
  | "NPC_INTENT_SESSION_ENDED"
  | "NPC_INTENT_STEP_LIMIT"
  | "NPC_INTENT_INTEGER_OVERFLOW";

export type NpcAdjudicationDecision =
  | { readonly status: "accepted"; readonly reason: "NPC_INTENT_ACCEPTED" }
  | { readonly status: "rejected"; readonly reason: NpcIntentRejectionReason };

export interface NpcRuntimeCue {
  readonly id: string;
  readonly channel: "visual" | "audio" | "ui";
  readonly intent: string;
}

export interface NpcRuntimeTransition {
  readonly transitionVersion: 1;
  readonly step: number;
  readonly from: { readonly kind: "node"; readonly index: number; readonly id: string };
  readonly actionId: string;
  readonly to: { readonly kind: "node" | "ending"; readonly index: number; readonly id: string };
  readonly emittedCues: readonly NpcRuntimeCue[];
}

export interface NpcAdjudicationResultDocument {
  readonly format: "matrix-oasis.npc-adjudication-result";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly timelineId: string;
  readonly intentId: string;
  readonly replayed: boolean;
  readonly revision: number;
  readonly headSha256: NpcAuthoritySha256 | null;
  readonly decision: NpcAdjudicationDecision;
  readonly beforeSnapshotSha256: NpcAuthoritySha256;
  readonly afterSnapshotSha256: NpcAuthoritySha256;
  readonly transition: NpcRuntimeTransition | null;
}

export interface WorldEventLedgerEntry {
  readonly revision: number;
  readonly intent: NpcIntent;
  readonly decision: NpcAdjudicationDecision;
  readonly beforeSnapshotSha256: NpcAuthoritySha256;
  readonly afterSnapshotSha256: NpcAuthoritySha256;
  readonly transition: NpcRuntimeTransition | null;
  readonly previousEntrySha256: NpcAuthoritySha256 | null;
  readonly entrySha256: NpcAuthoritySha256;
}

export interface WorldEventLedger {
  readonly format: "matrix-oasis.world-event-ledger";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly timeline: { readonly id: string; readonly stepLimit: number };
  readonly authority: {
    readonly runtime: NpcRuntimeIdentity;
    readonly policy: { readonly id: string; readonly contentVersion: string; readonly canonicalSha256: NpcAuthoritySha256 };
    readonly initialSnapshotSha256: NpcAuthoritySha256;
  };
  readonly revision: number;
  readonly headSha256: NpcAuthoritySha256 | null;
  readonly entries: readonly WorldEventLedgerEntry[];
}

export interface DerivedProjectionManifest {
  readonly format: "matrix-oasis.derived-projection-manifest";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly projectionKind: "memory" | "relationship";
  readonly reducer: { readonly id: string; readonly version: string; readonly sourceSha256: NpcAuthoritySha256 };
  readonly ledger: {
    readonly timelineId: string;
    readonly canonicalSha256: NpcAuthoritySha256;
    readonly throughRevision: number;
    readonly throughHeadSha256: NpcAuthoritySha256 | null;
  };
  readonly scopeEntityIds: readonly string[];
  readonly artifact: { readonly format: string; readonly byteLength: number; readonly sha256: NpcAuthoritySha256 };
}

export interface WorldEventLedgerReplayReport {
  readonly format: "matrix-oasis.world-event-ledger-replay-report";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly timelineId: string;
  readonly ledgerSha256: NpcAuthoritySha256;
  readonly throughRevision: number;
  readonly throughHeadSha256: NpcAuthoritySha256 | null;
  readonly verifiedEntries: number;
  readonly acceptedEntries: number;
  readonly rejectedEntries: number;
  readonly finalSnapshotSha256: NpcAuthoritySha256;
  readonly finalInspectionSha256: NpcAuthoritySha256;
}

export declare const NPC_AUTHORITY_FORMAT_VERSION: "0.1.0";
export declare const NPC_AUTHORITY_CANONICALIZATION: "matrix-oasis.canonical-json/1";
export declare const NPC_AUTHORITY_POLICY_FORMAT: "matrix-oasis.npc-authority-policy";
export declare const NPC_INTENT_FORMAT: "matrix-oasis.npc-intent";
export declare const NPC_ADJUDICATION_RESULT_FORMAT: "matrix-oasis.npc-adjudication-result";
export declare const WORLD_EVENT_LEDGER_FORMAT: "matrix-oasis.world-event-ledger";
export declare const DERIVED_PROJECTION_MANIFEST_FORMAT: "matrix-oasis.derived-projection-manifest";
export declare const WORLD_EVENT_LEDGER_REPLAY_REPORT_FORMAT: "matrix-oasis.world-event-ledger-replay-report";
export declare const NPC_AUTHORITY_LIMITS: Readonly<Record<string, number>>;
export declare const NPC_AUTHORITY_POLICY_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_INTENT_SCHEMA: Readonly<Record<string, unknown>>;
export declare const NPC_ADJUDICATION_RESULT_SCHEMA: Readonly<Record<string, unknown>>;
export declare const WORLD_EVENT_LEDGER_SCHEMA: Readonly<Record<string, unknown>>;
export declare const DERIVED_PROJECTION_MANIFEST_SCHEMA: Readonly<Record<string, unknown>>;
export declare const WORLD_EVENT_LEDGER_REPLAY_REPORT_SCHEMA: Readonly<Record<string, unknown>>;

export declare class NpcAuthorityContractOperationalError extends Error {
  readonly code: "NPC_AUTHORITY_CONTRACT_INTERNAL_ERROR";
}

export declare function validateNpcAuthorityPolicyJson(text: string): NpcAuthorityValidationReport;
export declare function validateNpcIntentJson(text: string): NpcAuthorityValidationReport;
export declare function validateNpcAdjudicationResultJson(text: string): NpcAuthorityValidationReport;
export declare function validateWorldEventLedgerJson(text: string): NpcAuthorityValidationReport;
export declare function validateDerivedProjectionManifestJson(text: string): NpcAuthorityValidationReport;
export declare function validateWorldEventLedgerReplayReportJson(text: string): NpcAuthorityValidationReport;
