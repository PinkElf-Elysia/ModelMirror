export type V2IsolationClass = "embedded-godot" | "service" | "asset";

export interface V2QualificationPlanResult {
  value: Readonly<Record<string, unknown>>;
  canonicalJson: string;
  sha256: string;
}

export declare class R18LandscapeHarnessError extends Error {
  readonly code: string;
}

export declare const V2_QUALIFICATION_PROFILES: Readonly<Record<V2IsolationClass, Readonly<Record<string, unknown>>>>;
export declare function createV2QualificationPlan(input: { candidate: unknown; laneIds: readonly string[] }): Readonly<V2QualificationPlanResult>;
export declare function validateV2QualificationPlan(text: string): Readonly<{ valid: boolean; value?: Readonly<Record<string, unknown>>; diagnostics: readonly string[] }>;
export declare function runV2Qualification(
  request: { planJson: string; sourceDir: string; outputDir: string; authorization: { candidateExecutionApproved: boolean; dependencyDownloadApproved: boolean; containerExecutionApproved: boolean } },
  operations: {
    inspectSource(input: Readonly<Record<string, unknown>>): Promise<unknown> | unknown;
    executeFixture(input: Readonly<Record<string, unknown>>): Promise<unknown> | unknown;
    inspectCleanup(input: Readonly<Record<string, unknown>>): Promise<unknown> | unknown;
  },
): Promise<Readonly<Record<string, unknown>>>;
export declare function publishV2QualificationEvidence(input: Readonly<Record<string, unknown>>): Readonly<Record<string, unknown>>;
export declare function verifyV2QualificationEvidenceDirectory(directory: string): Readonly<Record<string, unknown>>;
