import type {NpcBehaviorPolicy,NpcEntityBinding} from "@matrix-oasis/npc-behavior-contracts";
export declare const NPC_BEHAVIOR_MOTION_PROFILE:Readonly<Record<string,number>>;
export declare function synthesizeNpcBehaviorPolicy(input:{authorityPolicyJson:string}):Readonly<{ok:true;npcBehaviorPolicy:NpcBehaviorPolicy;canonicalNpcBehaviorPolicyJson:string}|{ok:false;diagnostics:readonly unknown[]}>;
export declare function synthesizeNpcEntityBindings(input:{sceneBlueprintJson:string;scenePackJson:string;assetBundleJson:string;spatialSolutionJson:string;spatialVerificationJson:string;authorityPolicyJson:string}):Readonly<{ok:true;npcEntityBindings:NpcEntityBinding;canonicalNpcEntityBindingJson:string}|{ok:false;diagnostics:readonly unknown[]}>;
declare const preparedBrand:unique symbol;export interface PreparedDeterministicNpcBehavior{readonly [preparedBrand]:true}
export declare function prepareDeterministicNpcBehavior(input:{behaviorPolicyJson:string;entityBindingJson:string;authorityPolicyJson:string}):unknown;
export declare function selectNextNpcBehaviorCommand(input:{prepared:PreparedDeterministicNpcBehavior;runtimeSnapshot:unknown;runtimeInspection:unknown;worldEventLedgerJson:string;behaviorState:unknown}):unknown;
