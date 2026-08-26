export type V2QualificationConclusion = "recommended" | "backup" | "deferred" | "rejected";
export interface V2Diagnostic { phase: string; severity: "error"; code: string; path: string; message: string; }
export interface V2ValidationReport<T = unknown> { reportVersion: 1; valid: boolean; diagnostics: readonly V2Diagnostic[]; value?: Readonly<T>; }
export declare function validateV2CandidateLockJson(text: string): V2ValidationReport;
export declare function validateV2QualificationReportJson(text: string): V2ValidationReport;
export declare function evaluateV2Candidate(report: unknown, policy?: { recommendedMinimum?: number; backupMinimum?: number }): Readonly<{ candidateId: string; lane: string; hardGatesPassed: boolean; total: number; conclusion: V2QualificationConclusion; switchConditions: readonly unknown[] }>;
export declare function rankV2Lane(evaluations: readonly unknown[]): readonly unknown[];
export declare const V2_CANDIDATE_LOCK_SCHEMA: Readonly<Record<string, unknown>>;
export declare const V2_QUALIFICATION_REPORT_SCHEMA: Readonly<Record<string, unknown>>;
