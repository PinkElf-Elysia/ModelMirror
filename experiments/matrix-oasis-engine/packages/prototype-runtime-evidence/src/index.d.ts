import type { PrototypeRuntimeReplayPlan, PrototypeRuntimeEvidence } from "@matrix-oasis/prototype-runtime-evidence-contracts";
export interface PrototypeRuntimeReplayRequest { readonly runtimeGamePackJson:string; readonly runtimeReceiptJson:string; readonly environmentFactsJson:string; readonly spatialIntentJson:string; readonly assetBundleJson:string; readonly spatialSolutionJson:string; readonly spatialVerificationReportJson:string }
export interface PrototypeRuntimeEvidenceDiagnostic { readonly phase:"planning"|"input"|"runtime"|"evidence"; readonly severity:"error"; readonly code:string; readonly path:string; readonly message:string }
export type PrototypeRuntimeReplayResult=Readonly<{ok:true;replayPlan:PrototypeRuntimeReplayPlan;canonicalReplayPlanJson:string}>|Readonly<{ok:false;diagnostics:readonly PrototypeRuntimeEvidenceDiagnostic[]}>;
export declare class PrototypeRuntimeEvidenceOperationalError extends Error { readonly code:"PROTOTYPE_RUNTIME_EVIDENCE_INTERNAL_ERROR" }
export declare function planPrototypeRuntimeReplay(request:PrototypeRuntimeReplayRequest):Promise<PrototypeRuntimeReplayResult>;
export declare function createGodotRuntimeEvidenceRunner(config:{readonly godotBin:string}):object;
export declare function collectPrototypeRuntimeEvidence(request:Readonly<{replayPlanJson:string;previewFiles:ReadonlyMap<string,Uint8Array>}>,runner:object):Promise<Readonly<{ok:true;evidence:PrototypeRuntimeEvidence;canonicalEvidenceJson:string;mediaFiles:ReadonlyMap<string,Uint8Array>}>|Readonly<{ok:false;diagnostics:readonly PrototypeRuntimeEvidenceDiagnostic[]}>>;
export declare function qualifyPrototypeRuntimeEvidence(request:unknown):Promise<unknown>;
