import type {RuntimeGameSessionInspection,RuntimeGameSessionSnapshot} from "@matrix-oasis/runtime-pack-simulator";
declare const brand:unique symbol;export interface NpcAuthoritySession{readonly [brand]:true}
export declare function createNpcAuthoritySession(input:{runtimeGamePackJson:string;runtimeReceiptJson:string;policyJson:string;timelineId:string;stepLimit?:number}):Promise<unknown>;
export declare function restoreNpcAuthoritySession(input:{runtimeGamePackJson:string;runtimeReceiptJson:string;policyJson:string;worldEventLedgerJson:string}):Promise<unknown>;
export declare function submitNpcAuthorityIntent(session:NpcAuthoritySession,npcIntentJson:string):unknown;
export declare function exportNpcAuthoritySession(session:NpcAuthoritySession):Readonly<{ok:true;runtimeSnapshot:RuntimeGameSessionSnapshot;inspection:RuntimeGameSessionInspection;canonicalWorldEventLedgerJson:string;fullReplayCount:number}|{ok:false;diagnostics:readonly unknown[]}>;
export declare function verifyNpcAuthoritySession(session:NpcAuthoritySession):unknown;
