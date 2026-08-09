import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  applyGameSessionAction,
  createGameSession,
  inspectGameSession,
  prepareAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-simulator";
import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  inspectRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import {
  captureJsonValue,
  cloneFrozen,
  deepFreeze,
  hasExactKeys,
  jsonEqual,
} from "./safety.mjs";

const preparedDataByHandle = new WeakMap();
const PARITY_SNAPSHOT_KEYS = Object.freeze([
  "snapshotVersion",
  "authoring",
  "runtime",
]);

const PARITY_DEFINITIONS = Object.freeze({
  PACK_PARITY_PREPARED_INVALID: Object.freeze({
    path: "/prepared",
    message: "The prepared parity handle is invalid.",
  }),
  PACK_PARITY_INVALID_SNAPSHOT: Object.freeze({
    path: "/snapshot",
    message: "The parity session snapshot is invalid.",
  }),
  PACK_PARITY_MISMATCH: Object.freeze({
    path: "/parity",
    message: "The Authoring and Runtime simulators produced different results.",
  }),
});

const RUNTIME_OPTIONS_INVALID = Object.freeze({
  ok: false,
  diagnostics: Object.freeze([
    Object.freeze({
      phase: "runtime",
      severity: "error",
      code: "PACK_RUNTIME_OPTIONS_INVALID",
      path: "/options",
      message: "The session options are invalid.",
    }),
  ]),
});

export class GamePackParityOperationalError extends Error {
  constructor() {
    super("PACK_PARITY_INTERNAL_ERROR");
    this.name = "GamePackParityOperationalError";
    this.code = "PACK_PARITY_INTERNAL_ERROR";
  }
}

function asOperational(error) {
  return error instanceof GamePackParityOperationalError
    ? error
    : new GamePackParityOperationalError();
}

function parityFailure(code) {
  const definition = PARITY_DEFINITIONS[code];
  if (!definition) {
    throw new GamePackParityOperationalError();
  }
  return deepFreeze({
    ok: false,
    diagnostics: [
      {
        phase: "parity",
        severity: "error",
        code,
        path: definition.path,
        message: definition.message,
      },
    ],
  });
}

function getPreparedData(prepared) {
  if (
    prepared === null ||
    (typeof prepared !== "object" && typeof prepared !== "function")
  ) {
    return undefined;
  }
  return preparedDataByHandle.get(prepared);
}

function safeOptions(options) {
  if (options === undefined) {
    return { ok: true, value: undefined };
  }
  const captured = captureJsonValue(options);
  if (!captured.ok) {
    return { ok: false };
  }
  return { ok: true, value: captured.value };
}

function safeParitySnapshot(snapshot) {
  const captured = captureJsonValue(snapshot);
  if (
    !captured.ok ||
    !hasExactKeys(captured.value, PARITY_SNAPSHOT_KEYS) ||
    captured.value.snapshotVersion !== 1
  ) {
    return undefined;
  }
  return captured.value;
}

function projectRuntimeInspection(inspection) {
  return {
    inspectionVersion: 1,
    pack: {
      format: inspection.pack.sourceFormat,
      formatVersion: inspection.pack.sourceFormatVersion,
      id: inspection.pack.id,
      contentVersion: inspection.pack.contentVersion,
      language: inspection.pack.language,
      title: inspection.pack.title,
      summary: inspection.pack.summary,
    },
    status: inspection.status,
    location: {
      kind: inspection.location.kind,
      id: inspection.location.id,
      title: inspection.location.title,
      text: inspection.location.text,
      entityIds: [...inspection.location.entityIds],
    },
    variables: inspection.variables.map((variable) => ({
      id: variable.id,
      type: variable.type,
      value: variable.value,
    })),
    actions: inspection.actions.map((action) => ({
      id: action.id,
      label: action.label,
      entityIds: [...action.entityIds],
      available: action.available,
    })),
    stepCount: inspection.stepCount,
    stepLimit: inspection.stepLimit,
  };
}

function projectRuntimeSnapshot(snapshot, inspection) {
  const variables = Object.create(null);
  for (const variable of inspection.variables) {
    variables[variable.id] = variable.value;
  }
  return {
    snapshotVersion: 1,
    pack: {
      format: snapshot.pack.sourceFormat,
      formatVersion: snapshot.pack.sourceFormatVersion,
      id: snapshot.pack.id,
      contentVersion: snapshot.pack.contentVersion,
    },
    status: snapshot.status,
    location: {
      kind: snapshot.location.kind,
      id: inspection.location.id,
    },
    variables,
    stepCount: snapshot.stepCount,
    stepLimit: snapshot.stepLimit,
  };
}

function projectRuntimeTransition(transition) {
  return {
    transitionVersion: 1,
    step: transition.step,
    from: { kind: "node", id: transition.from.id },
    actionId: transition.actionId,
    to: { kind: transition.to.kind, id: transition.to.id },
    emittedCues: transition.emittedCues.map((cue) => ({
      id: cue.id,
      channel: cue.channel,
      intent: cue.intent,
    })),
  };
}

function matchingFailure(authoring, runtime) {
  if (authoring?.ok === false && runtime?.ok === false && jsonEqual(authoring, runtime)) {
    return cloneFrozen(authoring);
  }
  return undefined;
}

function compositeSnapshot(authoring, runtime) {
  return {
    snapshotVersion: 1,
    authoring,
    runtime,
  };
}

function createResultsMatch(authoring, runtime) {
  return authoring?.ok === true &&
    runtime?.ok === true &&
    jsonEqual(
      authoring.snapshot,
      projectRuntimeSnapshot(runtime.snapshot, runtime.inspection),
    ) &&
    jsonEqual(authoring.inspection, projectRuntimeInspection(runtime.inspection)) &&
    jsonEqual(authoring.emittedCues, runtime.emittedCues);
}

function inspectResultsMatch(authoring, runtime) {
  return authoring?.ok === true &&
    runtime?.ok === true &&
    jsonEqual(authoring.inspection, projectRuntimeInspection(runtime.inspection));
}

function applyResultsMatch(authoring, runtime) {
  return authoring?.ok === true &&
    runtime?.ok === true &&
    jsonEqual(
      authoring.snapshot,
      projectRuntimeSnapshot(runtime.snapshot, runtime.inspection),
    ) &&
    jsonEqual(authoring.inspection, projectRuntimeInspection(runtime.inspection)) &&
    jsonEqual(authoring.transition, projectRuntimeTransition(runtime.transition));
}

export async function prepareGamePackParityJson(authoringText) {
  try {
    const compiled = await compileAuthoringGamePackJson(authoringText);
    if (!compiled || typeof compiled !== "object" || typeof compiled.ok !== "boolean") {
      throw new GamePackParityOperationalError();
    }
    if (!compiled.ok) {
      const validationReport = cloneFrozen(compiled.validationReport);
      if (!validationReport) {
        throw new GamePackParityOperationalError();
      }
      return deepFreeze({ ok: false, validationReport });
    }
    const authoringPrepared = prepareAuthoringGamePackJson(authoringText);
    if (!authoringPrepared?.ok) {
      throw new GamePackParityOperationalError();
    }
    const receiptJson = canonicalizeJsonValue(compiled.receipt);
    const runtimePrepared = await prepareRuntimeGamePackJson(
      compiled.canonicalJson,
      receiptJson,
    );
    if (!runtimePrepared?.ok) {
      throw new GamePackParityOperationalError();
    }
    const handle = Object.freeze(Object.create(null));
    const artifact = deepFreeze({
      artifactVersion: 1,
      runtimePackJson: compiled.canonicalJson,
      runtimePackReceiptJson: receiptJson,
    });
    preparedDataByHandle.set(
      handle,
      Object.freeze({
        authoringPrepared: authoringPrepared.prepared,
        runtimePrepared: runtimePrepared.prepared,
      }),
    );
    return deepFreeze({ ok: true, prepared: handle, artifact });
  } catch (error) {
    throw asOperational(error);
  }
}

export function createGamePackParitySession(prepared, options) {
  try {
    const data = getPreparedData(prepared);
    if (!data) {
      return parityFailure("PACK_PARITY_PREPARED_INVALID");
    }
    const capturedOptions = safeOptions(options);
    if (!capturedOptions.ok) {
      return RUNTIME_OPTIONS_INVALID;
    }
    const authoring = createGameSession(
      data.authoringPrepared,
      capturedOptions.value,
    );
    const runtime = createRuntimeGameSession(
      data.runtimePrepared,
      capturedOptions.value,
    );
    const failure = matchingFailure(authoring, runtime);
    if (failure) {
      return failure;
    }
    if (!createResultsMatch(authoring, runtime)) {
      return parityFailure("PACK_PARITY_MISMATCH");
    }
    return deepFreeze({
      ok: true,
      snapshot: compositeSnapshot(authoring.snapshot, runtime.snapshot),
      inspection: cloneFrozen(authoring.inspection),
      emittedCues: cloneFrozen(authoring.emittedCues),
    });
  } catch (error) {
    throw asOperational(error);
  }
}

export function inspectGamePackParitySession(prepared, snapshotInput) {
  try {
    const data = getPreparedData(prepared);
    if (!data) {
      return parityFailure("PACK_PARITY_PREPARED_INVALID");
    }
    const snapshot = safeParitySnapshot(snapshotInput);
    if (!snapshot) {
      return parityFailure("PACK_PARITY_INVALID_SNAPSHOT");
    }
    const authoring = inspectGameSession(data.authoringPrepared, snapshot.authoring);
    const runtime = inspectRuntimeGameSession(data.runtimePrepared, snapshot.runtime);
    const failure = matchingFailure(authoring, runtime);
    if (failure) {
      return failure;
    }
    if (!inspectResultsMatch(authoring, runtime)) {
      return parityFailure("PACK_PARITY_MISMATCH");
    }
    return deepFreeze({ ok: true, inspection: cloneFrozen(authoring.inspection) });
  } catch (error) {
    throw asOperational(error);
  }
}

export function applyGamePackParitySessionAction(
  prepared,
  snapshotInput,
  actionId,
) {
  try {
    const data = getPreparedData(prepared);
    if (!data) {
      return parityFailure("PACK_PARITY_PREPARED_INVALID");
    }
    const snapshot = safeParitySnapshot(snapshotInput);
    if (!snapshot) {
      return parityFailure("PACK_PARITY_INVALID_SNAPSHOT");
    }
    const stableActionId = typeof actionId === "string" ? actionId : undefined;
    const authoring = applyGameSessionAction(
      data.authoringPrepared,
      snapshot.authoring,
      stableActionId,
    );
    const runtime = applyRuntimeGameSessionAction(
      data.runtimePrepared,
      snapshot.runtime,
      stableActionId,
    );
    const failure = matchingFailure(authoring, runtime);
    if (failure) {
      return failure;
    }
    if (!applyResultsMatch(authoring, runtime)) {
      return parityFailure("PACK_PARITY_MISMATCH");
    }
    return deepFreeze({
      ok: true,
      snapshot: compositeSnapshot(authoring.snapshot, runtime.snapshot),
      inspection: cloneFrozen(authoring.inspection),
      transition: cloneFrozen(authoring.transition),
    });
  } catch (error) {
    throw asOperational(error);
  }
}
