export type Sha256=`sha256:${string}`;
export interface NpcBehaviorRule {readonly nodeId:string;readonly actionId:string;readonly executionLimit:number;readonly minimumRevisionGap:number}
export interface NpcBehaviorPolicy {readonly format:"matrix-oasis.npc-behavior-policy";readonly formatVersion:"0.1.0";readonly canonicalization:"matrix-oasis.canonical-json/1";readonly authorityPolicySha256:Sha256;readonly actors:readonly {readonly actorEntityId:string;readonly rules:readonly NpcBehaviorRule[]}[];readonly motion:Readonly<Record<string,number>>}
export interface NpcEntityBinding {readonly format:"matrix-oasis.npc-entity-binding";readonly formatVersion:"0.1.0";readonly canonicalization:"matrix-oasis.canonical-json/1";readonly identities:Readonly<Record<string,Sha256>>;readonly bindings:readonly {readonly actorEntityId:string;readonly assetBriefId:string;readonly placementId:string;readonly runtimeEntityId:string;readonly homeFloorAnchorId:string;readonly homePositionMm:Readonly<{x:number;y:number;z:number}>;readonly visibleNodeIds:readonly string[]}[]}
export interface NpcBehaviorValidationReport {readonly reportVersion:1;readonly valid:boolean;readonly diagnostics:readonly Readonly<{phase:string;severity:"error";code:string;path:string;message:string}>[]}
export declare const NPC_BEHAVIOR_FORMAT_VERSION:"0.1.0";
export declare const NPC_BEHAVIOR_LIMITS:Readonly<Record<string,number>>;
export declare const NPC_BEHAVIOR_POLICY_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_ENTITY_BINDING_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_BEHAVIOR_TRACE_SCHEMA:Readonly<Record<string,unknown>>;
export declare const NPC_ENTITY_BRIDGE_REPORT_SCHEMA:Readonly<Record<string,unknown>>;
export declare function validateNpcBehaviorPolicyJson(text:string):NpcBehaviorValidationReport;
export declare function validateNpcEntityBindingJson(text:string):NpcBehaviorValidationReport;
export declare function validateNpcBehaviorTraceJson(text:string):NpcBehaviorValidationReport;
export declare function validateNpcEntityBridgeReportJson(text:string):NpcBehaviorValidationReport;
