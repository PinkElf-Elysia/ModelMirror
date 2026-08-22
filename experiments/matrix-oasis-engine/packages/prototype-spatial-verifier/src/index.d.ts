import type { PrototypeSpatialSolution } from "@matrix-oasis/prototype-spatial-solution-contracts";

export interface PrototypeSpatialSolutionVerifierConfig {
  readonly godotBin: string;
}

export interface PrototypeSpatialSolutionVerificationRequest {
  readonly spatialIntentJson: string;
  readonly environmentFactsJson: string;
  readonly spatialSolutionJson: string;
  readonly assetBundleJson: string;
  readonly runtimeGamePackJson: string;
  readonly runtimeReceiptJson: string;
  readonly spatialAssemblyJson: string;
  readonly environmentColliderBytes: Uint8Array;
  readonly environmentSplatBytes: Uint8Array;
  readonly assetFiles: ReadonlyMap<string, Uint8Array>;
}

export interface PrototypeSpatialVerifierDiagnostic {
  readonly phase: "input" | "integrity" | "verification";
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeSpatialVerificationEvidence {
  readonly format: "matrix-oasis.godot-spatial-solution-verification";
  readonly formatVersion: "0.1.0";
  readonly solutionSha256: string;
  readonly placementCount: number;
  readonly nodeContextCount: number;
  readonly checkedPathCount: number;
  readonly checkedTerminalCount: number;
  readonly checkedVisualSafetyBoxCount: number;
  readonly allChecksPassed: true;
}

export type PrototypeSpatialSolutionVerificationResult =
  | Readonly<{
      ok: true;
      spatialSolution: PrototypeSpatialSolution;
      verification: PrototypeSpatialVerificationEvidence;
      canonicalVerificationReportJson: string;
    }>
  | Readonly<{ ok: false; diagnostics: readonly PrototypeSpatialVerifierDiagnostic[] }>;

export declare class PrototypeSpatialVerifierOperationalError extends Error {
  readonly code: "PROTOTYPE_SPATIAL_VERIFIER_INTERNAL_ERROR";
}

export declare function createGodotSpatialSolutionVerifier(
  config: PrototypeSpatialSolutionVerifierConfig,
): object;

export declare function verifyPrototypeSpatialSolution(
  request: PrototypeSpatialSolutionVerificationRequest,
  verifier: object,
): Promise<PrototypeSpatialSolutionVerificationResult>;
