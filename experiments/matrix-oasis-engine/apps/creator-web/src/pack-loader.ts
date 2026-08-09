import {
  createGamePackParitySession,
  prepareGamePackParityJson,
} from "@matrix-oasis/game-pack-parity-harness";
import type {
  ApplyGamePackParitySessionActionResult,
  CompiledRuntimeArtifact,
  CreateGamePackParitySessionResult,
  GamePackParitySnapshot,
  PrepareGamePackParityResult,
  PreparedGamePackParity,
} from "@matrix-oasis/game-pack-parity-harness";

export const MAX_LOCAL_PACK_BYTES = 1_048_576;

type ValidationReport = Extract<
  PrepareGamePackParityResult,
  { readonly ok: false }
>["validationReport"];
type ParityRuntimeFailure = Extract<
  CreateGamePackParitySessionResult,
  { readonly ok: false }
>;
type ParityCreateSuccess = Extract<
  CreateGamePackParitySessionResult,
  { readonly ok: true }
>;
type ParityApplySuccess = Extract<
  ApplyGamePackParitySessionActionResult,
  { readonly ok: true }
>;
type CueDescriptor = Extract<
  CreateGamePackParitySessionResult,
  { readonly ok: true }
>["emittedCues"][number];

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
  readonly prepared: PreparedGamePackParity;
  readonly artifact: CompiledRuntimeArtifact;
  readonly snapshot: GamePackParitySnapshot;
  readonly inspection: ParityCreateSuccess["inspection"];
  readonly emittedCues: readonly CueDescriptor[];
  readonly transition: ParityApplySuccess["transition"] | null;
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
  | ParityRuntimeFailure["diagnostics"][number];

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

export type PrepareCreatorSessionResult =
  | { readonly ok: true; readonly candidate: CreatorSessionBundle }
  | { readonly ok: false; readonly diagnostics: readonly PackLoadDiagnostic[] };

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

export async function prepareCreatorSession(
  text: string,
  source: CreatorSessionSource,
): Promise<PrepareCreatorSessionResult> {
  try {
    const preparedResult = await prepareGamePackParityJson(text);
    if (!preparedResult.ok) {
      return Object.freeze({
        ok: false as const,
        diagnostics: freezeDiagnostics(
          preparedResult.validationReport.diagnostics,
        ),
      });
    }
    const sessionResult = createGamePackParitySession(preparedResult.prepared);
    if (!sessionResult.ok) {
      return Object.freeze({
        ok: false as const,
        diagnostics: freezeDiagnostics(sessionResult.diagnostics),
      });
    }
    return Object.freeze({
      ok: true as const,
      candidate: Object.freeze({
        source: Object.freeze(source),
        prepared: preparedResult.prepared,
        artifact: preparedResult.artifact,
        snapshot: sessionResult.snapshot,
        inspection: sessionResult.inspection,
        emittedCues: sessionResult.emittedCues,
        transition: null,
      }),
    });
  } catch {
    return Object.freeze({
      ok: false as const,
      diagnostics: loaderDiagnostics("PACK_LOADER_PREPARATION_FAILED"),
    });
  }
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

    const prepared = await prepareCreatorSession(text, { kind: "local" });
    if (requestToken !== this.#latestRequestToken) {
      return stale(requestToken, activeSession);
    }
    return prepared.ok
      ? ready(requestToken, activeSession, prepared.candidate)
      : rejected(requestToken, activeSession, prepared.diagnostics);
  }
}
