import type { AuthoringGamePackValidationReport } from "@matrix-oasis/game-pack-validator";
import type {
  RuntimeGamePack,
  RuntimeGamePackReceipt,
} from "@matrix-oasis/runtime-pack-contracts";

export interface CompileAuthoringGamePackSuccess {
  readonly ok: true;
  readonly runtimePack: RuntimeGamePack;
  readonly canonicalJson: string;
  readonly receipt: RuntimeGamePackReceipt;
}

export interface CompileAuthoringGamePackFailure {
  readonly ok: false;
  readonly validationReport: AuthoringGamePackValidationReport;
}

export type CompileAuthoringGamePackResult =
  | CompileAuthoringGamePackSuccess
  | CompileAuthoringGamePackFailure;

export declare class GamePackCompilerOperationalError extends Error {
  readonly code: "PACK_COMPILER_INTERNAL_ERROR";
}

export declare function compileAuthoringGamePack(
  value: unknown,
): Promise<CompileAuthoringGamePackResult>;

export declare function compileAuthoringGamePackJson(
  text: string,
): Promise<CompileAuthoringGamePackResult>;
