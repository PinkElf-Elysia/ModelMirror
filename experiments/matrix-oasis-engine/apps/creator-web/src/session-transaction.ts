import {
  applyGameSessionAction,
  createGameSession,
} from "@matrix-oasis/game-pack-simulator";
import type { GameRuntimeDiagnostic } from "@matrix-oasis/game-pack-simulator";
import type { CreatorSessionBundle } from "./pack-loader";

export interface CreatorRuntimeInternalDiagnostic {
  readonly phase: "runtime";
  readonly severity: "error";
  readonly code: "PACK_RUNTIME_INTERNAL_ERROR";
  readonly path: "";
  readonly message: string;
}

export type CreatorSessionOperationDiagnostic =
  | GameRuntimeDiagnostic
  | CreatorRuntimeInternalDiagnostic;

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
    phase: "runtime" as const,
    severity: "error" as const,
    code: "PACK_RUNTIME_INTERNAL_ERROR" as const,
    path: "" as const,
    message: "The reference simulator could not complete the operation safely.",
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
  createSession: typeof createGameSession = createGameSession,
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
  applyAction: typeof applyGameSessionAction = applyGameSessionAction,
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
