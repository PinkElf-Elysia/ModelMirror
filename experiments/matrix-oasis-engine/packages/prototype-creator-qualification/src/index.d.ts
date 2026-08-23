import type { PrototypeCreatorQualification } from "@matrix-oasis/prototype-creator-qualification-contracts";

export type PrototypeCreatorQualificationReferenceVerifier = (request: Readonly<{
  qualification: PrototypeCreatorQualification;
  qualificationJson: string;
  qualificationRunId: string;
}>) => boolean | Readonly<{ valid: boolean }> | Promise<boolean | Readonly<{ valid: boolean }>>;

export interface PrototypeCreatorQualifiedRun {
  readonly qualificationRunId: string;
  readonly runDirectory: string;
  readonly qualificationJson: string;
  readonly qualification: PrototypeCreatorQualification;
}

export interface PrototypeCreatorQualificationRootRequest {
  readonly qualifiedRunRoot: string;
  readonly temporaryRoot: string;
  readonly verifyReferences: PrototypeCreatorQualificationReferenceVerifier;
}

export declare class PrototypeCreatorQualificationCacheOperationalError extends Error {
  readonly code:
    | "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID"
    | "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID"
    | "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_REFERENCE_INVALID"
    | "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INTERNAL_ERROR";
}

export declare function publishQualifiedCreatorRun(
  request: PrototypeCreatorQualificationRootRequest & Readonly<{ canonicalQualificationJson: string }>,
): Promise<Readonly<{ qualificationRunId: string; runDirectory: string }>>;

export declare function loadVerifiedQualifiedCreatorRun(
  request: PrototypeCreatorQualificationRootRequest & Readonly<{ qualificationRunId: string }>,
): Promise<PrototypeCreatorQualifiedRun>;

export declare function findVerifiedQualifiedCreatorRun(
  request: PrototypeCreatorQualificationRootRequest & Readonly<{ promptSha256: string; model: string }>,
): Promise<PrototypeCreatorQualifiedRun | null>;

export declare function recoverQualifiedCreatorRuns(
  request: PrototypeCreatorQualificationRootRequest,
): Promise<Readonly<{ currentQualificationRunId: string | null; runs: readonly PrototypeCreatorQualifiedRun[] }>>;
