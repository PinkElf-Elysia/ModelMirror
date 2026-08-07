import {
  createGameSession,
  prepareAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-simulator";
import type {
  CueDescriptor,
  GameRuntimeDiagnostic,
  GameSessionInspection,
  GameSessionSnapshot,
  GameSessionTransition,
  PrepareAuthoringGamePackResult,
  PreparedAuthoringGamePack,
} from "@matrix-oasis/game-pack-simulator";

export const MAX_LOCAL_PACK_BYTES = 1_048_576;

type ValidationReport = Extract<
  PrepareAuthoringGamePackResult,
  { readonly ok: false }
>["validationReport"];

export interface LocalPackFile {
  readonly name: string;
  readonly size: number;
  arrayBuffer(): Promise<ArrayBuffer>;
}

export type CreatorSessionSource =
  | { readonly kind: "local" }
  | { readonly kind: "builtin"; readonly id: string };

export interface CreatorSessionBundle {
  readonly source: CreatorSessionSource;
  readonly prepared: PreparedAuthoringGamePack;
  readonly snapshot: GameSessionSnapshot;
  readonly inspection: GameSessionInspection;
  readonly emittedCues: readonly CueDescriptor[];
  readonly transition: GameSessionTransition | null;
}

export type PackLoaderDiagnosticCode =
  | "PACK_LOADER_FILE_INVALID"
  | "PACK_LOADER_EXTENSION_INVALID"
  | "PACK_LOADER_FILE_TOO_LARGE"
  | "PACK_LOADER_FILE_CHANGED"
  | "PACK_LOADER_READ_FAILED"
  | "PACK_LOADER_UTF8_INVALID"
  | "PACK_LOADER_PREPARATION_FAILED";

export interface PackLoaderDiagnostic {
  readonly phase: "load";
  readonly severity: "error";
  readonly code: PackLoaderDiagnosticCode;
  readonly path: "/file";
  readonly message: string;
}

export type PackLoadDiagnostic =
  | PackLoaderDiagnostic
  | ValidationReport["diagnostics"][number]
  | GameRuntimeDiagnostic;

interface PackLoadResultBase {
  readonly requestToken: number;
  readonly activeSession: CreatorSessionBundle | null;
}

export interface PackLoadReadyResult extends PackLoadResultBase {
  readonly status: "ready";
  readonly candidate: CreatorSessionBundle;
}

export interface PackLoadRejectedResult extends PackLoadResultBase {
  readonly status: "rejected";
  readonly diagnostics: readonly PackLoadDiagnostic[];
}

export interface PackLoadStaleResult extends PackLoadResultBase {
  readonly status: "stale";
}

export type PackLoadResult =
  | PackLoadReadyResult
  | PackLoadRejectedResult
  | PackLoadStaleResult;

const DIAGNOSTIC_DEFINITIONS = Object.freeze({
  PACK_LOADER_FILE_INVALID: Object.freeze({
    message: "The selected file cannot be read as a local Pack.",
  }),
  PACK_LOADER_EXTENSION_INVALID: Object.freeze({
    message: "Select a JSON file.",
  }),
  PACK_LOADER_FILE_TOO_LARGE: Object.freeze({
    message: "The selected file exceeds the 1 MiB limit.",
  }),
  PACK_LOADER_FILE_CHANGED: Object.freeze({
    message: "The selected file changed while it was being read.",
  }),
  PACK_LOADER_READ_FAILED: Object.freeze({
    message: "The selected file could not be read.",
  }),
  PACK_LOADER_UTF8_INVALID: Object.freeze({
    message: "The selected file is not valid UTF-8.",
  }),
  PACK_LOADER_PREPARATION_FAILED: Object.freeze({
    message: "The local Pack could not be prepared safely.",
  }),
} satisfies Record<PackLoaderDiagnosticCode, { readonly message: string }>);

function loaderDiagnostics(
  code: PackLoaderDiagnosticCode,
): readonly PackLoaderDiagnostic[] {
  return Object.freeze([
    Object.freeze({
      phase: "load" as const,
      severity: "error" as const,
      code,
      path: "/file" as const,
      message: DIAGNOSTIC_DEFINITIONS[code].message,
    }),
  ]);
}

function freezeDiagnostics(
  diagnostics: readonly PackLoadDiagnostic[],
): readonly PackLoadDiagnostic[] {
  return Object.freeze([...diagnostics]);
}

function rejected(
  requestToken: number,
  activeSession: CreatorSessionBundle | null,
  diagnostics: readonly PackLoadDiagnostic[],
): PackLoadRejectedResult {
  return Object.freeze({
    status: "rejected" as const,
    requestToken,
    activeSession,
    diagnostics: freezeDiagnostics(diagnostics),
  });
}

function stale(
  requestToken: number,
  activeSession: CreatorSessionBundle | null,
): PackLoadStaleResult {
  return Object.freeze({
    status: "stale" as const,
    requestToken,
    activeSession,
  });
}

function ready(
  requestToken: number,
  activeSession: CreatorSessionBundle | null,
  candidate: CreatorSessionBundle,
): PackLoadReadyResult {
  return Object.freeze({
    status: "ready" as const,
    requestToken,
    activeSession,
    candidate,
  });
}

function validFileSize(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0;
}

export class LocalPackLoader {
  #latestRequestToken = 0;

  get latestRequestToken(): number {
    return this.#latestRequestToken;
  }

  async loadCandidate(
    file: LocalPackFile,
    activeSession: CreatorSessionBundle | null,
  ): Promise<PackLoadResult> {
    const requestToken = ++this.#latestRequestToken;
    let fileName: string;
    let beforeSize: number;

    try {
      fileName = file.name;
      beforeSize = file.size;
    } catch {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_FILE_INVALID"),
      );
    }

    if (typeof fileName !== "string" || !validFileSize(beforeSize)) {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_FILE_INVALID"),
      );
    }
    if (!fileName.toLowerCase().endsWith(".json")) {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_EXTENSION_INVALID"),
      );
    }
    if (beforeSize > MAX_LOCAL_PACK_BYTES) {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_FILE_TOO_LARGE"),
      );
    }

    let buffer: ArrayBuffer;
    try {
      buffer = await file.arrayBuffer();
    } catch {
      return requestToken === this.#latestRequestToken
        ? rejected(
            requestToken,
            activeSession,
            loaderDiagnostics("PACK_LOADER_READ_FAILED"),
          )
        : stale(requestToken, activeSession);
    }

    if (requestToken !== this.#latestRequestToken) {
      return stale(requestToken, activeSession);
    }

    let afterSize: number;
    try {
      afterSize = file.size;
    } catch {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_READ_FAILED"),
      );
    }
    if (
      !validFileSize(afterSize) ||
      !(buffer instanceof ArrayBuffer) ||
      !validFileSize(buffer.byteLength)
    ) {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_READ_FAILED"),
      );
    }
    if (
      afterSize > MAX_LOCAL_PACK_BYTES ||
      buffer.byteLength > MAX_LOCAL_PACK_BYTES
    ) {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_FILE_TOO_LARGE"),
      );
    }
    if (afterSize !== beforeSize || buffer.byteLength !== afterSize) {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_FILE_CHANGED"),
      );
    }

    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    } catch {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_UTF8_INVALID"),
      );
    }

    try {
      const preparedResult = prepareAuthoringGamePackJson(text);
      if (!preparedResult.ok) {
        return rejected(
          requestToken,
          activeSession,
          preparedResult.validationReport.diagnostics,
        );
      }
      const sessionResult = createGameSession(preparedResult.prepared);
      if (!sessionResult.ok) {
        return rejected(
          requestToken,
          activeSession,
          sessionResult.diagnostics,
        );
      }
      const candidate = Object.freeze({
        source: Object.freeze({ kind: "local" as const }),
        prepared: preparedResult.prepared,
        snapshot: sessionResult.snapshot,
        inspection: sessionResult.inspection,
        emittedCues: sessionResult.emittedCues,
        transition: null,
      });
      return requestToken === this.#latestRequestToken
        ? ready(requestToken, activeSession, candidate)
        : stale(requestToken, activeSession);
    } catch {
      return rejected(
        requestToken,
        activeSession,
        loaderDiagnostics("PACK_LOADER_PREPARATION_FAILED"),
      );
    }
  }
}
