import {
  applyGamePackParitySessionAction,
  createGamePackParitySession,
} from "@matrix-oasis/game-pack-parity-harness";
import type {
  ApplyGamePackParitySessionActionResult,
  CreateGamePackParitySessionResult,
} from "@matrix-oasis/game-pack-parity-harness";
import type { CreatorSessionBundle } from "./pack-loader";

type ParityOperationDiagnostic = Extract<
  | ApplyGamePackParitySessionActionResult
  | CreateGamePackParitySessionResult,
  { readonly ok: false }
>["diagnostics"][number];

export interface CreatorParityInternalDiagnostic {
  readonly phase: "parity";
  readonly severity: "error";
  readonly code: "PACK_PARITY_INTERNAL_ERROR";
  readonly path: "";
  readonly message: string;
}

export type CreatorSessionOperationDiagnostic =
  | ParityOperationDiagnostic
  | CreatorParityInternalDiagnostic;

export type CreatorSessionCandidateResult =
  | {
      readonly ok: true;
      readonly candidate: CreatorSessionBundle;
    }
  | {
      readonly ok: false;
      readonly diagnostics: readonly CreatorSessionOperationDiagnostic[];
    };

export interface SessionCommitDecision {
  readonly committed: boolean;
  readonly session: CreatorSessionBundle;
}

const INTERNAL_DIAGNOSTICS = Object.freeze([
  Object.freeze({
    phase: "parity" as const,
    severity: "error" as const,
    code: "PACK_PARITY_INTERNAL_ERROR" as const,
    path: "" as const,
    message: "The parity harness could not complete the operation safely.",
  }),
]);

function failure(
  diagnostics: readonly CreatorSessionOperationDiagnostic[],
): CreatorSessionCandidateResult {
  return Object.freeze({
    ok: false as const,
    diagnostics: Object.freeze([...diagnostics]),
  });
}

function success(candidate: CreatorSessionBundle): CreatorSessionCandidateResult {
  return Object.freeze({ ok: true as const, candidate });
}

export function resetSessionCandidate(
  baseSession: CreatorSessionBundle,
  createSession: typeof createGamePackParitySession =
    createGamePackParitySession,
): CreatorSessionCandidateResult {
  try {
    const created = createSession(baseSession.prepared);
    if (!created.ok) {
      return failure(created.diagnostics);
    }
    return success(
      Object.freeze({
        source: baseSession.source,
        prepared: baseSession.prepared,
        artifact: baseSession.artifact,
        snapshot: created.snapshot,
        inspection: created.inspection,
        emittedCues: created.emittedCues,
        transition: null,
      }),
    );
  } catch {
    return failure(INTERNAL_DIAGNOSTICS);
  }
}

export function applySessionActionCandidate(
  baseSession: CreatorSessionBundle,
  actionId: string,
  applyAction: typeof applyGamePackParitySessionAction =
    applyGamePackParitySessionAction,
): CreatorSessionCandidateResult {
  try {
    const applied = applyAction(
      baseSession.prepared,
      baseSession.snapshot,
      actionId,
    );
    if (!applied.ok) {
      return failure(applied.diagnostics);
    }
    return success(
      Object.freeze({
        source: baseSession.source,
        prepared: baseSession.prepared,
        artifact: baseSession.artifact,
        snapshot: applied.snapshot,
        inspection: applied.inspection,
        emittedCues: applied.transition.emittedCues,
        transition: applied.transition,
      }),
    );
  } catch {
    return failure(INTERNAL_DIAGNOSTICS);
  }
}

export function selectSessionCandidate(
  currentSession: CreatorSessionBundle,
  expectedSession: CreatorSessionBundle,
  candidate: CreatorSessionBundle,
): SessionCommitDecision {
  if (currentSession !== expectedSession) {
    return Object.freeze({ committed: false, session: currentSession });
  }
  return Object.freeze({ committed: true, session: candidate });
}
