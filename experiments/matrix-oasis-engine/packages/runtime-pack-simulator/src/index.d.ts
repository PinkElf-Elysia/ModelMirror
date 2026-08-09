import type { RuntimeGamePackValidationReport } from "@matrix-oasis/runtime-pack-validator";

declare const preparedRuntimeGamePackBrand: unique symbol;

export interface PreparedRuntimeGamePack {
  readonly [preparedRuntimeGamePackBrand]: true;
}

export type RuntimeSessionScalar = boolean | number | string;
export type RuntimeSessionVariableType = "boolean" | "integer" | "enum";
export type RuntimeSessionCueChannel = "visual" | "audio" | "ui";

export interface RuntimeSessionPackIdentity {
  readonly format: "matrix-oasis.runtime-game-pack";
  readonly formatVersion: "0.1.0";
  readonly sourceFormat: "matrix-oasis.authoring-game-pack";
  readonly sourceFormatVersion: "0.1.0";
  readonly id: string;
  readonly contentVersion: string;
  readonly sourceSha256: string;
  readonly artifactSha256: string;
}

export type RuntimeSessionLocation =
  | { readonly kind: "node"; readonly index: number }
  | { readonly kind: "ending"; readonly index: number };

export interface RuntimeGameSessionSnapshot {
  readonly snapshotVersion: 1;
  readonly pack: RuntimeSessionPackIdentity;
  readonly status: "active" | "ended";
  readonly location: RuntimeSessionLocation;
  readonly variables: readonly RuntimeSessionScalar[];
  readonly stepCount: number;
  readonly stepLimit: number;
}

export interface RuntimeSessionCueDescriptor {
  readonly id: string;
  readonly channel: RuntimeSessionCueChannel;
  readonly intent: string;
}

export interface RuntimeGameSessionInspection {
  readonly inspectionVersion: 1;
  readonly pack: RuntimeSessionPackIdentity & {
    readonly language: string;
    readonly title: string;
    readonly summary: string | null;
  };
  readonly status: "active" | "ended";
  readonly location: {
    readonly kind: "node" | "ending";
    readonly index: number;
    readonly id: string;
    readonly title: string;
    readonly text: string | null;
    readonly entityIds: readonly string[];
  };
  readonly variables: readonly {
    readonly id: string;
    readonly type: RuntimeSessionVariableType;
    readonly value: RuntimeSessionScalar;
  }[];
  readonly actions: readonly {
    readonly id: string;
    readonly label: string;
    readonly entityIds: readonly string[];
    readonly available: boolean;
  }[];
  readonly stepCount: number;
  readonly stepLimit: number;
}

export interface RuntimeGameSessionTransition {
  readonly transitionVersion: 1;
  readonly step: number;
  readonly from: {
    readonly kind: "node";
    readonly index: number;
    readonly id: string;
  };
  readonly actionId: string;
  readonly to: {
    readonly kind: "node" | "ending";
    readonly index: number;
    readonly id: string;
  };
  readonly emittedCues: readonly RuntimeSessionCueDescriptor[];
}

export type RuntimeSessionDiagnosticCode =
  | "PACK_RUNTIME_PREPARED_PACK_INVALID"
  | "PACK_RUNTIME_OPTIONS_INVALID"
  | "PACK_RUNTIME_INVALID_SNAPSHOT"
  | "PACK_RUNTIME_PACK_MISMATCH"
  | "PACK_RUNTIME_SESSION_ENDED"
  | "PACK_RUNTIME_STEP_LIMIT"
  | "PACK_RUNTIME_ACTION_UNKNOWN"
  | "PACK_RUNTIME_ACTION_UNAVAILABLE"
  | "PACK_RUNTIME_INTEGER_OVERFLOW";

export interface RuntimeSessionDiagnostic {
  readonly phase: "runtime";
  readonly severity: "error";
  readonly code: RuntimeSessionDiagnosticCode;
  readonly path: string;
  readonly message: string;
}

export interface RuntimeSessionFailure {
  readonly ok: false;
  readonly diagnostics: readonly RuntimeSessionDiagnostic[];
}

export interface PrepareRuntimeGamePackSuccess {
  readonly ok: true;
  readonly prepared: PreparedRuntimeGamePack;
}

export interface PrepareRuntimeGamePackFailure {
  readonly ok: false;
  readonly validationReport: RuntimeGamePackValidationReport;
}

export type PrepareRuntimeGamePackResult =
  | PrepareRuntimeGamePackSuccess
  | PrepareRuntimeGamePackFailure;

export interface CreateRuntimeGameSessionOptions {
  readonly stepLimit?: number;
}

export interface CreateRuntimeGameSessionSuccess {
  readonly ok: true;
  readonly snapshot: RuntimeGameSessionSnapshot;
  readonly inspection: RuntimeGameSessionInspection;
  readonly emittedCues: readonly RuntimeSessionCueDescriptor[];
}

export type CreateRuntimeGameSessionResult =
  | CreateRuntimeGameSessionSuccess
  | RuntimeSessionFailure;

export interface InspectRuntimeGameSessionSuccess {
  readonly ok: true;
  readonly inspection: RuntimeGameSessionInspection;
}

export type InspectRuntimeGameSessionResult =
  | InspectRuntimeGameSessionSuccess
  | RuntimeSessionFailure;

export interface ApplyRuntimeGameSessionActionSuccess {
  readonly ok: true;
  readonly snapshot: RuntimeGameSessionSnapshot;
  readonly inspection: RuntimeGameSessionInspection;
  readonly transition: RuntimeGameSessionTransition;
}

export type ApplyRuntimeGameSessionActionResult =
  | ApplyRuntimeGameSessionActionSuccess
  | RuntimeSessionFailure;

export declare class RuntimeGamePackSimulatorOperationalError extends Error {
  readonly code: "PACK_RUNTIME_INTERNAL_ERROR";
}

export declare function prepareRuntimeGamePackJson(
  runtimeText: string,
  receiptText: string,
): Promise<PrepareRuntimeGamePackResult>;

export declare function createRuntimeGameSession(
  prepared: PreparedRuntimeGamePack,
  options?: CreateRuntimeGameSessionOptions,
): CreateRuntimeGameSessionResult;

export declare function inspectRuntimeGameSession(
  prepared: PreparedRuntimeGamePack,
  snapshot: unknown,
): InspectRuntimeGameSessionResult;

export declare function applyRuntimeGameSessionAction(
  prepared: PreparedRuntimeGamePack,
  snapshot: unknown,
  actionId: unknown,
): ApplyRuntimeGameSessionActionResult;
