import type {
  NpcAdjudicationDecision,
  NpcAuthorityDiagnostic,
  NpcRuntimeTransition,
  WorldEventLedgerEntry,
} from "@matrix-oasis/npc-authority-contracts";
import type {
  RuntimeGameSessionInspection,
  RuntimeGameSessionSnapshot,
} from "@matrix-oasis/runtime-pack-simulator";

declare const preparedNpcAuthorityBrand: unique symbol;
export interface PreparedNpcAuthority {
  readonly [preparedNpcAuthorityBrand]: true;
}

declare const incrementalNpcAuthorityBrand: unique symbol;
export interface IncrementalNpcAuthorityState {readonly [incrementalNpcAuthorityBrand]: true}

export interface NpcAuthorityFailure {
  readonly ok: false;
  readonly diagnostics: readonly (NpcAuthorityDiagnostic | {
    readonly phase: "runtime";
    readonly severity: "error";
    readonly code: string;
    readonly path: string;
    readonly message: string;
  })[];
}

export interface PrepareNpcAuthoritySuccess {
  readonly ok: true;
  readonly prepared: PreparedNpcAuthority;
}

export interface NpcAuthorityTimelineSuccess {
  readonly ok: true;
  readonly runtimeSnapshot: RuntimeGameSessionSnapshot;
  readonly inspection: RuntimeGameSessionInspection;
  readonly canonicalWorldEventLedgerJson: string;
}

export interface NpcAdjudicationSuccess {
  readonly ok: true;
  readonly replayed: boolean;
  readonly canonicalAdjudicationResultJson: string;
  readonly runtimeSnapshot: RuntimeGameSessionSnapshot;
  readonly canonicalWorldEventLedgerJson: string;
}

export interface WorldEventLedgerReplaySuccess {
  readonly ok: true;
  readonly runtimeSnapshot: RuntimeGameSessionSnapshot;
  readonly inspection: RuntimeGameSessionInspection;
  readonly canonicalWorldEventLedgerJson: string;
  readonly canonicalWorldEventLedgerReplayReportJson: string;
}

export interface DerivedProjectionManifestSuccess {
  readonly ok: true;
  readonly canonicalDerivedProjectionManifestJson: string;
}

export declare class NpcAuthorityRuntimeOperationalError extends Error {
  readonly code: "NPC_AUTHORITY_INTERNAL_ERROR";
}

export declare function prepareNpcAuthority(input: {
  readonly runtimeGamePackJson: string;
  readonly runtimeReceiptJson: string;
  readonly policyJson: string;
}): Promise<PrepareNpcAuthoritySuccess | NpcAuthorityFailure>;

export declare function createNpcAuthorityTimeline(
  prepared: PreparedNpcAuthority,
  options: { readonly timelineId: string; readonly stepLimit?: number },
): NpcAuthorityTimelineSuccess | NpcAuthorityFailure;

export declare function adjudicateNpcIntent(input: {
  readonly prepared: PreparedNpcAuthority;
  readonly runtimeSnapshot: RuntimeGameSessionSnapshot;
  readonly worldEventLedgerJson: string;
  readonly npcIntentJson: string;
}): NpcAdjudicationSuccess | NpcAuthorityFailure;

export declare function replayWorldEventLedger(input: {
  readonly prepared: PreparedNpcAuthority;
  readonly worldEventLedgerJson: string;
}): WorldEventLedgerReplaySuccess | NpcAuthorityFailure;

export declare function createNpcAuthorityIncrementalState(input:{readonly prepared:PreparedNpcAuthority;readonly worldEventLedgerJson:string}):({readonly ok:true;readonly state:IncrementalNpcAuthorityState;readonly runtimeSnapshot:RuntimeGameSessionSnapshot;readonly inspection:RuntimeGameSessionInspection;readonly canonicalWorldEventLedgerJson:string}|NpcAuthorityFailure);
export declare function submitNpcAuthorityIncrementalIntent(input:{readonly state:IncrementalNpcAuthorityState;readonly npcIntentJson:string}):((NpcAdjudicationSuccess&{readonly inspection:RuntimeGameSessionInspection;readonly fullReplayPerformed:boolean})|NpcAuthorityFailure);
export declare function exportNpcAuthorityIncrementalState(state:IncrementalNpcAuthorityState):({readonly ok:true;readonly runtimeSnapshot:RuntimeGameSessionSnapshot;readonly inspection:RuntimeGameSessionInspection;readonly canonicalWorldEventLedgerJson:string;readonly fullReplayCount:number}|NpcAuthorityFailure);
export declare function verifyNpcAuthorityIncrementalState(state:IncrementalNpcAuthorityState):((WorldEventLedgerReplaySuccess&{readonly fullReplayCount:number})|NpcAuthorityFailure);

export declare function createDerivedProjectionManifest(input: {
  readonly worldEventLedgerJson: string;
  readonly projectionKind: "memory" | "relationship";
  readonly reducer: { readonly id: string; readonly version: string; readonly sourceSha256: string };
  readonly scopeEntityIds: readonly string[];
  readonly artifact: { readonly format: string; readonly bytes: string | Uint8Array };
}): DerivedProjectionManifestSuccess | NpcAuthorityFailure;

export declare function createWorldEventLedgerCore(input: {
  readonly policyJson: string;
  readonly timelineId: string;
  readonly stepLimit: number;
  readonly initialSnapshotSha256: string;
}): { readonly ok: true; readonly canonicalWorldEventLedgerJson: string } | NpcAuthorityFailure;

export declare function resolveWorldEventLedgerIntent(input: {
  readonly worldEventLedgerJson: string;
  readonly npcIntentJson: string;
}):
  | { readonly ok: true; readonly kind: "missing" }
  | { readonly ok: true; readonly kind: "replay"; readonly entry: WorldEventLedgerEntry }
  | NpcAuthorityFailure;

export declare function appendWorldEventLedgerEntryCore(input: {
  readonly worldEventLedgerJson: string;
  readonly npcIntentJson: string;
  readonly decision: NpcAdjudicationDecision;
  readonly beforeSnapshotSha256: string;
  readonly afterSnapshotSha256: string;
  readonly transition: NpcRuntimeTransition | null;
}):
  | { readonly ok: true; readonly kind: "appended"; readonly entry: WorldEventLedgerEntry; readonly canonicalWorldEventLedgerJson: string }
  | { readonly ok: true; readonly kind: "replay"; readonly entry: WorldEventLedgerEntry }
  | NpcAuthorityFailure;

export declare function hashCanonicalValue(value: unknown): string;
export declare function isNpcAuthorityId(value: unknown): boolean;
export declare function isNpcAuthoritySha256(value: unknown): boolean;
