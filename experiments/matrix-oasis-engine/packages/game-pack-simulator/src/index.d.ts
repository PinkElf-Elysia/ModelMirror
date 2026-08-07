import type { AuthoringGamePackValidationReport } from "@matrix-oasis/game-pack-validator";

declare const preparedAuthoringGamePackBrand: unique symbol;

export interface PreparedAuthoringGamePack {
  readonly [preparedAuthoringGamePackBrand]: true;
}

export type GameScalarValue = boolean | number | string;
export type GameVariableType = "boolean" | "integer" | "enum";
export type CueChannel = "visual" | "audio" | "ui";

export interface GamePackIdentity {
  readonly format: string;
  readonly formatVersion: string;
  readonly id: string;
  readonly contentVersion: string;
}

export type GameSessionLocation =
  | { readonly kind: "node"; readonly id: string }
  | { readonly kind: "ending"; readonly id: string };

export interface GameSessionSnapshot {
  readonly snapshotVersion: 1;
  readonly pack: GamePackIdentity;
  readonly status: "active" | "ended";
  readonly location: GameSessionLocation;
  readonly variables: Readonly<Record<string, GameScalarValue>>;
  readonly stepCount: number;
  readonly stepLimit: number;
}

export interface CueDescriptor {
  readonly id: string;
  readonly channel: CueChannel;
  readonly intent: string;
}

export interface GameSessionInspection {
  readonly inspectionVersion: 1;
  readonly pack: GamePackIdentity & {
    readonly language: string;
    readonly title: string;
    readonly summary: string | null;
  };
  readonly status: "active" | "ended";
  readonly location: {
    readonly kind: "node" | "ending";
    readonly id: string;
    readonly title: string;
    readonly text: string | null;
    readonly entityIds: readonly string[];
  };
  readonly variables: readonly {
    readonly id: string;
    readonly type: GameVariableType;
    readonly value: GameScalarValue;
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

export interface GameSessionTransition {
  readonly transitionVersion: 1;
  readonly step: number;
  readonly from: { readonly kind: "node"; readonly id: string };
  readonly actionId: string;
  readonly to: GameSessionLocation;
  readonly emittedCues: readonly CueDescriptor[];
}

export type GameRuntimeDiagnosticCode =
  | "PACK_RUNTIME_PREPARED_PACK_INVALID"
  | "PACK_RUNTIME_OPTIONS_INVALID"
  | "PACK_RUNTIME_INVALID_SNAPSHOT"
  | "PACK_RUNTIME_PACK_MISMATCH"
  | "PACK_RUNTIME_SESSION_ENDED"
  | "PACK_RUNTIME_STEP_LIMIT"
  | "PACK_RUNTIME_ACTION_UNKNOWN"
  | "PACK_RUNTIME_ACTION_UNAVAILABLE"
  | "PACK_RUNTIME_INTEGER_OVERFLOW";

export interface GameRuntimeDiagnostic {
  readonly phase: "runtime";
  readonly severity: "error";
  readonly code: GameRuntimeDiagnosticCode;
  readonly path: string;
  readonly message: string;
}

export interface GameRuntimeFailure {
  readonly ok: false;
  readonly diagnostics: readonly GameRuntimeDiagnostic[];
}

export interface PrepareAuthoringGamePackSuccess {
  readonly ok: true;
  readonly prepared: PreparedAuthoringGamePack;
}

export interface PrepareAuthoringGamePackFailure {
  readonly ok: false;
  readonly validationReport: AuthoringGamePackValidationReport;
}

export type PrepareAuthoringGamePackResult =
  | PrepareAuthoringGamePackSuccess
  | PrepareAuthoringGamePackFailure;

export interface CreateGameSessionOptions {
  readonly stepLimit?: number;
}

export interface CreateGameSessionSuccess {
  readonly ok: true;
  readonly snapshot: GameSessionSnapshot;
  readonly inspection: GameSessionInspection;
  readonly emittedCues: readonly CueDescriptor[];
}

export type CreateGameSessionResult = CreateGameSessionSuccess | GameRuntimeFailure;

export interface InspectGameSessionSuccess {
  readonly ok: true;
  readonly inspection: GameSessionInspection;
}

export type InspectGameSessionResult = InspectGameSessionSuccess | GameRuntimeFailure;

export interface ApplyGameSessionActionSuccess {
  readonly ok: true;
  readonly snapshot: GameSessionSnapshot;
  readonly inspection: GameSessionInspection;
  readonly transition: GameSessionTransition;
}

export type ApplyGameSessionActionResult =
  | ApplyGameSessionActionSuccess
  | GameRuntimeFailure;

export declare class GamePackSimulatorOperationalError extends Error {
  readonly code: "PACK_RUNTIME_INTERNAL_ERROR";
}

export declare function prepareAuthoringGamePack(
  value: unknown,
): PrepareAuthoringGamePackResult;

export declare function prepareAuthoringGamePackJson(
  text: string,
): PrepareAuthoringGamePackResult;

export declare function createGameSession(
  prepared: PreparedAuthoringGamePack,
  options?: CreateGameSessionOptions,
): CreateGameSessionResult;

export declare function inspectGameSession(
  prepared: PreparedAuthoringGamePack,
  snapshot: unknown,
): InspectGameSessionResult;

export declare function applyGameSessionAction(
  prepared: PreparedAuthoringGamePack,
  snapshot: unknown,
  actionId: unknown,
): ApplyGameSessionActionResult;
