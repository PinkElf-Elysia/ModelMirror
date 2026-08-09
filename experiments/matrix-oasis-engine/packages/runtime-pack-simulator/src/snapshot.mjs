import { canonicalScalar, captureJsonValue, hasExactKeys } from "./safety.mjs";

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
  "sourceFormat",
  "sourceFormatVersion",
  "id",
  "contentVersion",
  "sourceSha256",
  "artifactSha256",
]);
const LOCATION_KEYS = Object.freeze(["kind", "index"]);

function validStepLimit(value) {
  return Number.isSafeInteger(value) && value >= 1 && value <= MAX_STEP_LIMIT;
}

export function captureSessionOptions(options) {
  if (options === undefined) {
    return { ok: true, value: undefined, stepLimit: DEFAULT_STEP_LIMIT };
  }
  const captured = captureJsonValue(options);
  if (
    !captured.ok ||
    captured.value === null ||
    typeof captured.value !== "object" ||
    Array.isArray(captured.value)
  ) {
    return { ok: false };
  }
  const keys = Object.keys(captured.value);
  if (keys.length === 0) {
    return { ok: true, value: captured.value, stepLimit: DEFAULT_STEP_LIMIT };
  }
  if (!hasExactKeys(captured.value, ["stepLimit"])) {
    return { ok: false };
  }
  if (!validStepLimit(captured.value.stepLimit)) {
    return { ok: false };
  }
  return {
    ok: true,
    value: captured.value,
    stepLimit: captured.value.stepLimit,
  };
}

export function makePackIdentity(data) {
  return {
    format: data.pack.format,
    formatVersion: data.pack.formatVersion,
    sourceFormat: data.pack.source.format,
    sourceFormatVersion: data.pack.source.formatVersion,
    id: data.pack.source.id,
    contentVersion: data.pack.source.contentVersion,
    sourceSha256: data.pack.source.canonicalSha256,
    artifactSha256: data.receipt.artifact.sha256,
  };
}

export function makeInitialVariables(pack) {
  return pack.variables.map((variable) => canonicalScalar(variable.initial));
}

export function makeSnapshot({ data, status, location, variables, stepCount, stepLimit }) {
  return {
    snapshotVersion: 1,
    pack: makePackIdentity(data),
    status,
    location: { kind: location.kind, index: location.index },
    variables: variables.map((value) => canonicalScalar(value)),
    stepCount,
    stepLimit,
  };
}

function identityHasStrings(identity) {
  return IDENTITY_KEYS.every((key) => typeof identity[key] === "string");
}

function identityMatches(data, identity) {
  const expected = makePackIdentity(data);
  return IDENTITY_KEYS.every((key) => expected[key] === identity[key]);
}

function validVariableValue(variable, value) {
  if (variable.type === "boolean") {
    return typeof value === "boolean";
  }
  if (variable.type === "integer") {
    return Number.isSafeInteger(value);
  }
  return typeof value === "string" && variable.allowedValues.includes(value);
}

function locationIsValid(data, snapshot) {
  if (!hasExactKeys(snapshot.location, LOCATION_KEYS)) {
    return false;
  }
  const index = snapshot.location.index;
  if (!Number.isSafeInteger(index) || index < 0) {
    return false;
  }
  if (snapshot.status === "active") {
    return snapshot.location.kind === "node" && index < data.pack.nodes.length;
  }
  return snapshot.status === "ended" &&
    snapshot.location.kind === "ending" &&
    index < data.pack.endings.length;
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
  if (!identityMatches(data, snapshot.pack)) {
    return { ok: false, code: "PACK_RUNTIME_PACK_MISMATCH" };
  }
  if (
    (snapshot.status !== "active" && snapshot.status !== "ended") ||
    !locationIsValid(data, snapshot) ||
    !Array.isArray(snapshot.variables) ||
    snapshot.variables.length !== data.pack.variables.length ||
    !Number.isSafeInteger(snapshot.stepCount) ||
    snapshot.stepCount < 0 ||
    !validStepLimit(snapshot.stepLimit) ||
    snapshot.stepCount > snapshot.stepLimit
  ) {
    return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
  }
  for (let index = 0; index < data.pack.variables.length; index += 1) {
    if (!validVariableValue(data.pack.variables[index], snapshot.variables[index])) {
      return { ok: false, code: "PACK_RUNTIME_INVALID_SNAPSHOT" };
    }
  }
  return { ok: true, snapshot };
}
