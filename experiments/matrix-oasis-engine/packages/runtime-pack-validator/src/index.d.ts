export type RuntimeGamePackDiagnosticPhase =
  | "parse"
  | "schema"
  | "semantic"
  | "integrity";

export interface RuntimeGamePackDiagnosticLocation {
  readonly line: number;
  readonly column: number;
}

export interface RuntimeGamePackDiagnostic {
  readonly phase: RuntimeGamePackDiagnosticPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
  readonly relatedPath?: string;
  readonly location?: RuntimeGamePackDiagnosticLocation;
}

export interface RuntimeGamePackValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly RuntimeGamePackDiagnostic[];
}

export declare class RuntimeGamePackValidatorOperationalError extends Error {
  readonly code: "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR";
}

export declare function validateRuntimeGamePackJson(
  runtimeText: string,
  receiptText: string,
): Promise<RuntimeGamePackValidationReport>;
