import type {
  GameRuntimeFailure,
  GameSessionInspection,
  GameSessionSnapshot,
  GameSessionTransition,
} from "@matrix-oasis/game-pack-simulator";
import type { CompileAuthoringGamePackFailure } from "@matrix-oasis/game-pack-compiler";
import type { RuntimeGameSessionSnapshot } from "@matrix-oasis/runtime-pack-simulator";

declare const preparedGamePackParityBrand: unique symbol;

export interface PreparedGamePackParity {
  readonly [preparedGamePackParityBrand]: true;
}

export interface CompiledRuntimeArtifact {
  readonly artifactVersion: 1;
  readonly runtimePackJson: string;
  readonly runtimePackReceiptJson: string;
}

export interface GamePackParitySnapshot {
  readonly snapshotVersion: 1;
  readonly authoring: GameSessionSnapshot;
  readonly runtime: RuntimeGameSessionSnapshot;
}

export type GamePackParityDiagnosticCode =
  | "PACK_PARITY_PREPARED_INVALID"
  | "PACK_PARITY_INVALID_SNAPSHOT"
  | "PACK_PARITY_MISMATCH";

export interface GamePackParityDiagnostic {
  readonly phase: "parity";
  readonly severity: "error";
  readonly code: GamePackParityDiagnosticCode;
  readonly path: string;
  readonly message: string;
}

export interface GamePackParityFailure {
  readonly ok: false;
  readonly diagnostics: readonly GamePackParityDiagnostic[];
}

export interface PrepareGamePackParitySuccess {
  readonly ok: true;
  readonly prepared: PreparedGamePackParity;
  readonly artifact: CompiledRuntimeArtifact;
}

export interface PrepareGamePackParityFailure {
  readonly ok: false;
  readonly validationReport: CompileAuthoringGamePackFailure["validationReport"];
}

export type PrepareGamePackParityResult =
  | PrepareGamePackParitySuccess
  | PrepareGamePackParityFailure;

export interface CreateGamePackParitySessionSuccess {
  readonly ok: true;
  readonly snapshot: GamePackParitySnapshot;
  readonly inspection: GameSessionInspection;
  readonly emittedCues: readonly {
    readonly id: string;
    readonly channel: "visual" | "audio" | "ui";
    readonly intent: string;
  }[];
}

export type GamePackParityRuntimeFailure = GameRuntimeFailure | GamePackParityFailure;
export type CreateGamePackParitySessionResult =
  | CreateGamePackParitySessionSuccess
  | GamePackParityRuntimeFailure;

export interface InspectGamePackParitySessionSuccess {
  readonly ok: true;
  readonly inspection: GameSessionInspection;
}

export type InspectGamePackParitySessionResult =
  | InspectGamePackParitySessionSuccess
  | GamePackParityRuntimeFailure;

export interface ApplyGamePackParitySessionActionSuccess {
  readonly ok: true;
  readonly snapshot: GamePackParitySnapshot;
  readonly inspection: GameSessionInspection;
  readonly transition: GameSessionTransition;
}

export type ApplyGamePackParitySessionActionResult =
  | ApplyGamePackParitySessionActionSuccess
  | GamePackParityRuntimeFailure;

export declare class GamePackParityOperationalError extends Error {
  readonly code: "PACK_PARITY_INTERNAL_ERROR";
}

export declare function prepareGamePackParityJson(
  authoringText: string,
): Promise<PrepareGamePackParityResult>;

export declare function createGamePackParitySession(
  prepared: PreparedGamePackParity,
  options?: { readonly stepLimit?: number },
): CreateGamePackParitySessionResult;

export declare function inspectGamePackParitySession(
  prepared: PreparedGamePackParity,
  snapshot: unknown,
): InspectGamePackParitySessionResult;

export declare function applyGamePackParitySessionAction(
  prepared: PreparedGamePackParity,
  snapshot: unknown,
  actionId: unknown,
): ApplyGamePackParitySessionActionResult;
