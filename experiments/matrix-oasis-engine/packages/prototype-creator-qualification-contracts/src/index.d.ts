export type PrototypeCreatorQualificationPhase =
  | "parse"
  | "schema"
  | "semantic"
  | "integrity";

export interface PrototypeCreatorQualificationDiagnostic {
  readonly phase: PrototypeCreatorQualificationPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeCreatorQualificationValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly PrototypeCreatorQualificationDiagnostic[];
}

export interface PrototypeCreatorQualificationHashes {
  readonly runtimePackSha256: string;
  readonly runtimeReceiptSha256: string;
  readonly spatialIntentSha256: string;
  readonly environmentFactsSha256: string;
  readonly assetBundleSha256: string;
  readonly spatialSolutionSha256: string;
  readonly spatialVerificationSha256: string;
  readonly replayPlanSha256: string;
  readonly runtimeEvidenceSha256: string;
}

export interface PrototypeCreatorQualification {
  readonly format: "matrix-oasis.prototype-creator-qualification";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly profile: "matrix-oasis.creator-solved-evidence/1";
  readonly status: "qualified";
  readonly promptSha256: string;
  readonly model: string;
  readonly sourceRunId: string;
  readonly hashes: Readonly<PrototypeCreatorQualificationHashes>;
  readonly toolchain: Readonly<{
    godotVersion: "4.6.3";
    renderer: "forward_plus";
    evidenceProfile: "matrix-oasis.runtime-replay/1";
  }>;
  readonly evidence: Readonly<{
    runId: string;
    attempt: 0 | 1 | 2;
    replayCount: number;
    screenshotCount: number;
    videoCount: 1;
    sampleCount: 300;
    medianFrameMicros: number;
    medianFpsMilli: number;
  }>;
}

export declare const PROTOTYPE_CREATOR_QUALIFICATION_FORMAT:
  "matrix-oasis.prototype-creator-qualification";
export declare const PROTOTYPE_CREATOR_QUALIFICATION_FORMAT_VERSION: "0.1.0";
export declare const PROTOTYPE_CREATOR_QUALIFICATION_CANONICALIZATION:
  "matrix-oasis.canonical-json/1";
export declare const PROTOTYPE_CREATOR_QUALIFICATION_PROFILE:
  "matrix-oasis.creator-solved-evidence/1";
export declare const PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_PROFILE:
  "matrix-oasis.runtime-replay/1";
export declare const PROTOTYPE_CREATOR_QUALIFICATION_LIMITS: Readonly<
  Record<string, number>
>;
export declare const PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA: Readonly<
  Record<string, unknown>
>;

export declare class PrototypeCreatorQualificationContractOperationalError extends Error {
  readonly code: "PROTOTYPE_CREATOR_QUALIFICATION_CONTRACT_INTERNAL_ERROR";
}

export declare function validatePrototypeCreatorQualificationJson(
  text: string,
): PrototypeCreatorQualificationValidationReport;
