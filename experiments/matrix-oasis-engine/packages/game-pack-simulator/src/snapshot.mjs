import {
  canonicalScalar,
  captureJsonValue,
  hasExactKeys,
  makeNullRecord,
} from "./safety.mjs";

export const DEFAULT_STEP_LIMIT = 256;
export const MAX_STEP_LIMIT = 10_000;

const SNAPSHOT_KEYS = Object.freeze([
  "snapshotVersion",
  "pack",
  "status",
  "location",
  "variables",
  "stepCount",
  "stepLimit",
]);
const IDENTITY_KEYS = Object.freeze([
  "format",
  "formatVersion",
  "id",
  "contentVersion",
]);
const LOCATION_KEYS = Object.freeze(["kind", "id"]);

function isValidStepLimit(value) {
  return Number.isSafeInteger(value) && value >= 1 && value <= MAX_STEP_LIMIT;
}

export function captureSessionOptions(options) {
  if (options === undefined) {
    return { ok: true, stepLimit: DEFAULT_STEP_LIMIT };
  }
  const captured = captureJsonValue(options);
  if (!captured.ok || captured.value === null || Array.isArray(captured.value)) {
    return { ok: false };
  }
  const keys = Object.keys(captured.value);
  if (keys.length === 0) {
    return { ok: true, stepLimit: DEFAULT_STEP_LIMIT };
  }
  if (
    keys.length !== 1 ||
    keys[0] !== "stepLimit" ||
    !isValidStepLimit(captured.value.stepLimit)
  ) {
    return { ok: false };
  }
  return { ok: true, stepLimit: captured.value.stepLimit };
}

export function makePackIdentity(pack) {
  return {
    format: pack.format,
    formatVersion: pack.formatVersion,
    id: pack.id,
    contentVersion: pack.contentVersion,
  };
}

export function makeInitialVariables(pack) {
  return makeNullRecord(
    pack.variables.map((variable) => [variable.id, canonicalScalar(variable.initial)]),
  );
}

export function makeSnapshot({ data, status, location, variables, stepCount, stepLimit }) {
  return {
    snapshotVersion: 1,
    pack: makePackIdentity(data.pack),
    status,
    location: { kind: location.kind, id: location.id },
    variables: makeNullRecord(
      data.pack.variables.map((variable) => [
        variable.id,
        canonicalScalar(variables[variable.id]),
      ]),
    ),
    stepCount,
    stepLimit,
  };
}

function identityHasStrings(identity) {
  return IDENTITY_KEYS.every((key) => typeof identity[key] === "string");
}

function identityMatches(pack, identity) {
  return pack.format === identity.format &&
    pack.formatVersion === identity.formatVersion &&
    pack.id === identity.id &&
    pack.contentVersion === identity.contentVersion;
}

function variableValueIsValid(variable, value) {
  if (variable.type === "boolean") {
    return typeof value === "boolean";
  }
  if (variable.type === "integer") {
    return Number.isSafeInteger(value);
  }
  return typeof value === "string" && variable.allowedValues.includes(value);
}

export function validateSnapshot(data, input) {
  const captured = captureJsonValue(input);
  if (!captured.ok || !hasExactKeys(captured.value, SNAPSHOT_KEYS)) {
    return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
  }
  const snapshot = captured.value;
  if (
    snapshot.snapshotVersion !== 1 ||
    !hasExactKeys(snapshot.pack, IDENTITY_KEYS) ||
    !identityHasStrings(snapshot.pack)
  ) {
    return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
  }
  if (!identityMatches(data.pack, snapshot.pack)) {
    return { ok: false, code: "PACK_RUNTIME_PACK_MISMATCH" };
  }
  if (
    !hasExactKeys(snapshot.location, LOCATION_KEYS) ||
    typeof snapshot.location.id !== "string" ||
    (snapshot.status !== "active" && snapshot.status !== "ended") ||
    !Number.isSafeInteger(snapshot.stepCount) ||
    snapshot.stepCount < 0 ||
    !isValidStepLimit(snapshot.stepLimit) ||
    snapshot.stepCount > snapshot.stepLimit ||
    snapshot.variables === null ||
    typeof snapshot.variables !== "object" ||
    Array.isArray(snapshot.variables)
  ) {
    return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
  }
  if (
    (snapshot.status === "active" &&
      (snapshot.location.kind !== "node" || !data.nodes.has(snapshot.location.id))) ||
    (snapshot.status === "ended" &&
      (snapshot.location.kind !== "ending" || !data.endings.has(snapshot.location.id)))
  ) {
    return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
  }

  const variableKeys = Object.keys(snapshot.variables);
  if (variableKeys.length !== data.variables.size) {
    return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
  }
  for (const variable of data.pack.variables) {
    if (
      !Object.hasOwn(snapshot.variables, variable.id) ||
      !variableValueIsValid(variable, snapshot.variables[variable.id])
    ) {
      return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
    }
  }
  return { ok: true, snapshot };
}
