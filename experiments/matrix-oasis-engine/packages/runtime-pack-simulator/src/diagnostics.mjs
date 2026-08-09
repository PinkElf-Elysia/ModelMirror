import { deepFreeze } from "./safety.mjs";

const DEFINITIONS = Object.freeze({
  PACK_RUNTIME_PREPARED_PACK_INVALID: Object.freeze({
    path: "/prepared",
    message: "The prepared game pack handle is invalid.",
  }),
  PACK_RUNTIME_OPTIONS_INVALID: Object.freeze({
    path: "/options",
    message: "The session options are invalid.",
  }),
  PACK_RUNTIME_INVALID_SNAPSHOT: Object.freeze({
    path: "/snapshot",
    message: "The game session snapshot is invalid.",
  }),
  PACK_RUNTIME_PACK_MISMATCH: Object.freeze({
    path: "/snapshot/pack",
    message: "The game session snapshot belongs to a different pack.",
  }),
  PACK_RUNTIME_SESSION_ENDED: Object.freeze({
    path: "/snapshot/status",
    message: "The game session has already ended.",
  }),
  PACK_RUNTIME_STEP_LIMIT: Object.freeze({
    path: "/snapshot/stepCount",
    message: "The game session step limit has been reached.",
  }),
  PACK_RUNTIME_ACTION_UNKNOWN: Object.freeze({
    path: "/actionId",
    message: "The requested action is not declared at the current node.",
  }),
  PACK_RUNTIME_ACTION_UNAVAILABLE: Object.freeze({
    path: "/actionId",
    message: "The requested action is not currently available.",
  }),
  PACK_RUNTIME_INTEGER_OVERFLOW: Object.freeze({
    path: "/snapshot/variables",
    message: "The action would produce an integer outside the safe range.",
  }),
});

export class RuntimeGamePackSimulatorOperationalError extends Error {
  constructor() {
    super("PACK_RUNTIME_INTERNAL_ERROR");
    this.name = "RuntimeGamePackSimulatorOperationalError";
    this.code = "PACK_RUNTIME_INTERNAL_ERROR";
  }
}

export function asOperationalError(error) {
  return error instanceof RuntimeGamePackSimulatorOperationalError
    ? error
    : new RuntimeGamePackSimulatorOperationalError();
}

export function runtimeFailure(code) {
  const definition = DEFINITIONS[code];
  if (!definition) {
    throw new RuntimeGamePackSimulatorOperationalError();
  }
  return deepFreeze({
    ok: false,
    diagnostics: [
      {
        phase: "runtime",
        severity: "error",
        code,
        path: definition.path,
        message: definition.message,
      },
    ],
  });
}
