export type DiagnosticPhase = "parse" | "schema" | "semantic";

export interface DiagnosticLocation {
  readonly line: number;
  readonly column: number;
}

export interface AuthoringGamePackDiagnostic {
  readonly phase: DiagnosticPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
  readonly relatedPath?: string;
  readonly location?: DiagnosticLocation;
}

export interface AuthoringGamePackValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly AuthoringGamePackDiagnostic[];
}

export declare class AuthoringGamePackOperationalError extends Error {
  readonly code: "PACK_VALIDATOR_INTERNAL_ERROR";
}

export declare function validateAuthoringGamePack(
  value: unknown,
): AuthoringGamePackValidationReport;

export declare function validateAuthoringGamePackJson(
  text: string,
): AuthoringGamePackValidationReport;
