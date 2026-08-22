export type PrototypeRuntimeEvidencePhase = "parse" | "schema" | "semantic" | "integrity";
export interface PrototypeRuntimeEvidenceDiagnostic { readonly phase: PrototypeRuntimeEvidencePhase; readonly severity: "error"; readonly code: string; readonly path: string; readonly message: string }
export interface PrototypeRuntimeEvidenceValidationReport { readonly reportVersion: 1; readonly valid: boolean; readonly diagnostics: readonly PrototypeRuntimeEvidenceDiagnostic[] }
export interface PrototypeRuntimeReplayPlan { readonly format: "matrix-oasis.prototype-runtime-replay-plan"; readonly formatVersion: "0.1.0"; readonly canonicalization: "matrix-oasis.canonical-json/1"; readonly identity: Readonly<Record<string,string>>; readonly profile: Readonly<{id:"matrix-oasis.runtime-replay/1";maxReplays:32;maxActionsPerReplay:256;maxSemanticStates:100000}>; readonly coverage: Readonly<Record<string,number|string>>; readonly replays: readonly Readonly<{id:string;kind:"ending"|"loop"|"node-coverage"|"disabled-action"|"reset-ending"|"reset-active";actionIds:readonly string[];probeActionId:string|null;targetId:string|null;resetAfter:boolean;expectedLocationIds:readonly string[]}>[] }
export interface PrototypeRuntimeEvidence { readonly format: "matrix-oasis.prototype-runtime-evidence"; readonly formatVersion: "0.1.0"; readonly canonicalization: "matrix-oasis.canonical-json/1"; readonly replayPlanSha256:string; readonly identity:Readonly<Record<string,string>>; readonly attempt:0|1|2; readonly status:"passed"|"failed"; readonly observations:readonly unknown[]; readonly performance:Readonly<{sampleCount:300;medianFrameMicros:number;medianFpsMilli:number}>; readonly media:Readonly<{screenshots:readonly unknown[];videos:readonly unknown[]}>; readonly repairs:readonly unknown[] }
export declare const PROTOTYPE_RUNTIME_REPLAY_PLAN_FORMAT:"matrix-oasis.prototype-runtime-replay-plan";
export declare const PROTOTYPE_RUNTIME_EVIDENCE_FORMAT:"matrix-oasis.prototype-runtime-evidence";
export declare const PROTOTYPE_RUNTIME_EVIDENCE_FORMAT_VERSION:"0.1.0";
export declare const PROTOTYPE_RUNTIME_EVIDENCE_CANONICALIZATION:"matrix-oasis.canonical-json/1";
export declare const PROTOTYPE_RUNTIME_REPLAY_PROFILE:Readonly<Record<string,string|number>>;
export declare const PROTOTYPE_RUNTIME_EVIDENCE_LIMITS:Readonly<Record<string,number>>;
export declare const PROTOTYPE_RUNTIME_REPLAY_PLAN_SCHEMA:Readonly<Record<string,unknown>>;
export declare const PROTOTYPE_RUNTIME_EVIDENCE_SCHEMA:Readonly<Record<string,unknown>>;
export declare class PrototypeRuntimeEvidenceContractOperationalError extends Error { readonly code:"PROTOTYPE_RUNTIME_EVIDENCE_CONTRACT_INTERNAL_ERROR" }
export declare function validatePrototypeRuntimeReplayPlanJson(text:string):PrototypeRuntimeEvidenceValidationReport;
export declare function validatePrototypeRuntimeEvidenceJson(text:string):PrototypeRuntimeEvidenceValidationReport;
