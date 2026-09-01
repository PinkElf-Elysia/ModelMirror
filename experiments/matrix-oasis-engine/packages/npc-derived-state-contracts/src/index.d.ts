export type Sha256 = `sha256:${string}`;
export type Canonicalization = "matrix-oasis.canonical-json/1";
export type AuthorityIdentity = Readonly<{runtimePackSha256:Sha256;runtimeReceiptSha256:Sha256;authorityPolicySha256:Sha256;npcEntityBindingSha256:Sha256}>;
export type ReducerIdentity = Readonly<{id:string;version:string;sourceSha256:Sha256}>;
export type LedgerIdentity = Readonly<{timelineId:string;canonicalSha256:Sha256;throughRevision:number;throughHeadSha256:Sha256|null}>;
export type NpcDerivedStateProfile = Readonly<{
  timelineMode:"single";authorityMode:"runtime-and-ledger-only";personaMode:"trusted-static-seed";
  memoryScope:"actor-self-accepted-actions";relationshipScope:"accepted-explicit-policy-rules";
  deletionMode:"whole-derived-state";selectiveForgetting:false;externalModelCalls:false;semanticRetrieval:false;
}>;
export interface NpcPersonaSeed {
  readonly format:"matrix-oasis.npc-persona-seed";readonly formatVersion:"0.1.0";readonly canonicalization:Canonicalization;
  readonly id:string;readonly contentVersion:string;readonly authority:AuthorityIdentity;readonly traitIds:readonly string[];
  readonly actors:readonly Readonly<{actorEntityId:string;traits:readonly Readonly<{traitId:string;value:number}>[]}>[];
}
export interface NpcRelationshipProjectionPolicy {
  readonly format:"matrix-oasis.npc-relationship-projection-policy";readonly formatVersion:"0.1.0";readonly canonicalization:Canonicalization;
  readonly id:string;readonly contentVersion:string;readonly authority:AuthorityIdentity;readonly personaSeedSha256:Sha256;
  readonly repeatMode:"first-accepted-per-rule-actor-target-timeline";
  readonly rules:readonly Readonly<{ruleId:string;sourceActorEntityId:string;targetEntityId:string;nodeId:string;actionId:string;dimensionId:string;delta:number}>[];
}
export type NpcMemoryTransition = Readonly<{
  transitionVersion:1;step:number;from:Readonly<{kind:"node";index:number;id:string}>;actionId:string;
  to:Readonly<{kind:"node"|"ending";index:number;id:string}>;
}>;
export interface NpcMemoryProjection {
  readonly format:"matrix-oasis.npc-memory-projection";readonly formatVersion:"0.1.0";readonly canonicalization:Canonicalization;
  readonly authority:AuthorityIdentity;readonly personaSeedSha256:Sha256;readonly ledger:LedgerIdentity;readonly reducer:ReducerIdentity;
  readonly scopeActorEntityIds:readonly string[];
  readonly episodes:readonly Readonly<{
    episodeId:string;actorEntityId:string;intentId:string;revision:number;entrySha256:Sha256;
    beforeSnapshotSha256:Sha256;afterSnapshotSha256:Sha256;interactionEntityIds:readonly string[];transition:NpcMemoryTransition;
  }>[];
}
export interface NpcRelationshipProjection {
  readonly format:"matrix-oasis.npc-relationship-projection";readonly formatVersion:"0.1.0";readonly canonicalization:Canonicalization;
  readonly authority:AuthorityIdentity;readonly personaSeedSha256:Sha256;readonly relationshipPolicySha256:Sha256;
  readonly ledger:LedgerIdentity;readonly reducer:ReducerIdentity;readonly scopeActorEntityIds:readonly string[];
  readonly relationships:readonly Readonly<{
    sourceActorEntityId:string;targetEntityId:string;dimensionId:string;value:number;
    contributions:readonly Readonly<{ruleId:string;revision:number;entrySha256:Sha256;delta:number}>[];
  }>[];
}
export type DerivedArtifactReference = Readonly<{format:string;canonicalSha256:Sha256;byteLength:number}>;
export interface NpcDerivedStateBundle {
  readonly format:"matrix-oasis.npc-derived-state-bundle";readonly formatVersion:"0.1.0";readonly canonicalization:Canonicalization;
  readonly source:Readonly<{r20CurrentSha256:Sha256;r20AuthorityManifestSha256:Sha256;r20QualificationReceiptSha256:Sha256;npcEntityBindingSha256:Sha256}>;
  readonly authority:AuthorityIdentity;readonly ledger:LedgerIdentity;
  readonly replay:Readonly<{reportSha256:Sha256;finalSnapshotSha256:Sha256;finalInspectionSha256:Sha256}>;
  readonly reducers:Readonly<{memory:ReducerIdentity;relationship:ReducerIdentity}>;readonly profile:NpcDerivedStateProfile;
  readonly artifacts:Readonly<{personaSeed:DerivedArtifactReference;relationshipPolicy:DerivedArtifactReference;memoryProjection:DerivedArtifactReference;relationshipProjection:DerivedArtifactReference;memoryManifest:DerivedArtifactReference;relationshipManifest:DerivedArtifactReference}>;
}
export type NpcProjectionRebuildEvidence = Readonly<{personaSeedSha256:Sha256;relationshipPolicySha256:Sha256;replayReportSha256:Sha256;memoryProjectionSha256:Sha256;relationshipProjectionSha256:Sha256;memoryManifestSha256:Sha256;relationshipManifestSha256:Sha256;bundleSha256:Sha256}>;
export interface NpcProjectionQualificationReport {
  readonly format:"matrix-oasis.npc-projection-qualification-report";readonly formatVersion:"0.1.0";readonly canonicalization:Canonicalization;
  readonly qualifiedBundleSha256:Sha256;readonly ledger:LedgerIdentity;readonly profile:NpcDerivedStateProfile;
  readonly rebuilds:Readonly<{initial:NpcProjectionRebuildEvidence;repeated:NpcProjectionRebuildEvidence;afterDeletion:NpcProjectionRebuildEvidence;repeatedBuildCount:20}>;
  readonly deletion:Readonly<{mode:"whole-derived-state";derivedArtifactsRemoved:true;runtimeSnapshotSha256Before:Sha256;runtimeSnapshotSha256After:Sha256;ledgerSha256Before:Sha256;ledgerSha256After:Sha256}>;
  readonly counts:Readonly<{ledgerEntries:number;acceptedEntries:number;rejectedEntries:number;memoryEpisodes:number;relationshipEdges:number;relationshipContributions:number}>;
  readonly isolation:Readonly<{externalModelCalls:0;networkRequests:0;credentialReads:0}>;
  readonly markers:readonly ["R21_LEDGER_REBUILD_EQUIVALENT","R21_MEMORY_DELETION_VERIFIED","R21_RELATIONSHIP_PROJECTION_DETERMINISTIC"];
}
export type NpcDerivedStateValidationReport = Readonly<{reportVersion:1;valid:boolean;diagnostics:readonly Readonly<{phase:"parse"|"schema"|"semantic"|"canonical";severity:"error";code:string;path:string;message:string}>[]}>;
export declare const NPC_DERIVED_STATE_FORMAT_VERSION:"0.1.0";
export declare const NPC_DERIVED_STATE_CANONICALIZATION:Canonicalization;
export declare const NPC_PERSONA_SEED_FORMAT:"matrix-oasis.npc-persona-seed";
export declare const NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT:"matrix-oasis.npc-relationship-projection-policy";
export declare const NPC_MEMORY_PROJECTION_FORMAT:"matrix-oasis.npc-memory-projection";
export declare const NPC_RELATIONSHIP_PROJECTION_FORMAT:"matrix-oasis.npc-relationship-projection";
export declare const NPC_DERIVED_STATE_BUNDLE_FORMAT:"matrix-oasis.npc-derived-state-bundle";
export declare const NPC_PROJECTION_QUALIFICATION_REPORT_FORMAT:"matrix-oasis.npc-projection-qualification-report";
export declare const NPC_DERIVED_STATE_LIMITS:Readonly<Record<string,number>>;
export declare const NPC_PERSONA_SEED_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_RELATIONSHIP_PROJECTION_POLICY_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_MEMORY_PROJECTION_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_RELATIONSHIP_PROJECTION_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_DERIVED_STATE_BUNDLE_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_PROJECTION_QUALIFICATION_REPORT_SCHEMA:Readonly<Record<string,unknown>>;
export declare class NpcDerivedStateContractOperationalError extends Error {readonly code:"NPC_DERIVED_STATE_CONTRACT_INTERNAL_ERROR"}
export declare function validateNpcPersonaSeedJson(text:string):NpcDerivedStateValidationReport;
export declare function validateNpcRelationshipProjectionPolicyJson(text:string):NpcDerivedStateValidationReport;
export declare function validateNpcMemoryProjectionJson(text:string):NpcDerivedStateValidationReport;
export declare function validateNpcRelationshipProjectionJson(text:string):NpcDerivedStateValidationReport;
export declare function validateNpcDerivedStateBundleJson(text:string):NpcDerivedStateValidationReport;
export declare function validateNpcProjectionQualificationReportJson(text:string):NpcDerivedStateValidationReport;
