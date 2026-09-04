declare const preparedNpcDerivedStateBrand: unique symbol;
export interface PreparedNpcDerivedState {readonly [preparedNpcDerivedStateBrand]:true}

export interface NpcDerivedStateFailure {
  readonly ok:false;
  readonly diagnostics:readonly Readonly<{readonly phase:string;readonly severity:"error";readonly code:string;readonly path:string;readonly message:string}>[];
}

export interface NpcDerivedStateProjectionSuccess {
  readonly ok:true;
  readonly canonicalWorldEventLedgerReplayReportJson:string;
  readonly canonicalNpcMemoryProjectionJson:string;
  readonly canonicalNpcRelationshipProjectionJson:string;
  readonly canonicalMemoryDerivedProjectionManifestJson:string;
  readonly canonicalRelationshipDerivedProjectionManifestJson:string;
}

export interface NpcDerivedStateVerificationSuccess extends NpcDerivedStateProjectionSuccess {
  readonly canonicalNpcDerivedStateBundleJson:string;
}

export declare const NPC_DERIVED_STATE_REDUCERS:Readonly<{
  memory:Readonly<{id:"npc-memory-actor-self-actions";version:"0.1.0";sourceSha256:`sha256:${string}`}>;
  relationship:Readonly<{id:"npc-relationship-explicit-first-accepted";version:"0.1.0";sourceSha256:`sha256:${string}`}>;
}>;
export declare const NPC_DERIVED_STATE_PROFILE:Readonly<{
  timelineMode:"single";authorityMode:"runtime-and-ledger-only";personaMode:"trusted-static-seed";
  memoryScope:"actor-self-accepted-actions";relationshipScope:"accepted-explicit-policy-rules";
  deletionMode:"whole-derived-state";selectiveForgetting:false;externalModelCalls:false;semanticRetrieval:false;
}>;

export declare class NpcDerivedStateRuntimeOperationalError extends Error {readonly code:"NPC_DERIVED_STATE_INTERNAL_ERROR"}

export declare function prepareNpcDerivedState(input:{
  readonly runtimeGamePackJson:string;
  readonly runtimeReceiptJson:string;
  readonly authorityPolicyJson:string;
  readonly npcEntityBindingJson:string;
  readonly personaSeedJson:string;
  readonly relationshipPolicyJson:string;
}):Promise<{readonly ok:true;readonly prepared:PreparedNpcDerivedState}|NpcDerivedStateFailure>;

export declare function projectNpcDerivedState(input:{
  readonly prepared:PreparedNpcDerivedState;
  readonly worldEventLedgerJson:string;
}):NpcDerivedStateProjectionSuccess|NpcDerivedStateFailure;

export declare function verifyNpcDerivedState(input:{
  readonly prepared:PreparedNpcDerivedState;
  readonly worldEventLedgerJson:string;
  readonly memoryProjectionJson:string;
  readonly relationshipProjectionJson:string;
  readonly memoryManifestJson:string;
  readonly relationshipManifestJson:string;
  readonly derivedStateBundleJson:string;
}):NpcDerivedStateVerificationSuccess|NpcDerivedStateFailure;
