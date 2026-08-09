import path from "node:path";

export const MAX_AUTHORING_PACK_BYTES = 1024 * 1024;
export const MAX_RUNTIME_PACK_BYTES = 16 * 1024 * 1024;
export const MAX_RUNTIME_RECEIPT_BYTES = 16 * 1024;
export const RUNTIME_PACK_FILE_NAME = "runtime-game-pack.json";
export const RUNTIME_PACK_RECEIPT_FILE_NAME =
  "runtime-game-pack-receipt.json";

const RECEIPT_FORMAT = "matrix-oasis.runtime-game-pack-receipt";
const RECEIPT_FORMAT_VERSION = "0.1.0";
const CANONICALIZATION_PROFILE = "matrix-oasis.canonical-json/1";
const COMPILER_ID = "@matrix-oasis/game-pack-compiler";
const COMPILER_VERSION = "0.1.0-r3";
const RUNTIME_PACK_FORMAT = "matrix-oasis.runtime-game-pack";
const RUNTIME_PACK_FORMAT_VERSION = "0.1.0";

const WINDOWS_RESERVED_SLUGS = new Set([
  "aux",
  "con",
  "nul",
  "prn",
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
]);

const PUBLIC_POINTER_TOKENS = new Set([
  "actions",
  "allowedValues",
  "artifact",
  "byteLength",
  "canonicalization",
  "canonicalSha256",
  "channel",
  "compiler",
  "condition",
  "conditions",
  "contentVersion",
  "cueId",
  "cueIds",
  "cueIndex",
  "cueIndexes",
  "cues",
  "description",
  "effects",
  "endings",
  "entities",
  "entityIds",
  "entityIndexes",
  "entryCueIds",
  "entryCueIndexes",
  "entryNodeId",
  "entryNodeIndex",
  "format",
  "formatVersion",
  "id",
  "index",
  "initial",
  "intent",
  "kind",
  "label",
  "language",
  "nodes",
  "op",
  "receipt",
  "runtimePack",
  "sha256",
  "source",
  "summary",
  "target",
  "text",
  "title",
  "type",
  "value",
  "variableId",
  "variableIndex",
  "variables",
  "version",
  "when",
]);

const AUTHORING_DIAGNOSTIC_CODES = new Set([
  "PACK_INPUT_TOO_LARGE",
  "PACK_INPUT_UTF8_INVALID",
  "PACK_JSON_DUPLICATE_KEY",
  "PACK_JSON_INPUT_TYPE",
  "PACK_JSON_SYNTAX",
  "PACK_SCHEMA_CONST",
  "PACK_SCHEMA_DUPLICATE_ITEM",
  "PACK_SCHEMA_ENUM",
  "PACK_SCHEMA_FORBIDDEN_VALUE",
  "PACK_SCHEMA_INVALID",
  "PACK_SCHEMA_MAX_ITEMS",
  "PACK_SCHEMA_MIN_ITEMS",
  "PACK_SCHEMA_NON_JSON_VALUE",
  "PACK_SCHEMA_NUMBER_CONSTRAINT",
  "PACK_SCHEMA_REQUIRED",
  "PACK_SCHEMA_SHAPE",
  "PACK_SCHEMA_STRING_CONSTRAINT",
  "PACK_SCHEMA_TYPE",
  "PACK_SCHEMA_UNKNOWN_PROPERTY",
  "PACK_ACTION_ID_DUPLICATE",
  "PACK_CONDITION_DEPTH_EXCEEDED",
  "PACK_CONDITION_VALUE_TYPE_MISMATCH",
  "PACK_CONDITION_VARIABLE_TYPE_MISMATCH",
  "PACK_CUE_REFERENCE_UNKNOWN",
  "PACK_EFFECT_VALUE_TYPE_MISMATCH",
  "PACK_EFFECT_VARIABLE_TYPE_MISMATCH",
  "PACK_ENTITY_REFERENCE_UNKNOWN",
  "PACK_ENTRY_NODE_UNKNOWN",
  "PACK_ENUM_INITIAL_NOT_ALLOWED",
  "PACK_ENUM_VALUE_NOT_ALLOWED",
  "PACK_NODE_NO_ENDING_PATH",
  "PACK_NODE_UNREACHABLE",
  "PACK_TARGET_REFERENCE_UNKNOWN",
  "PACK_TOP_LEVEL_ID_DUPLICATE",
  "PACK_VARIABLE_REFERENCE_UNKNOWN",
]);

const RUNTIME_DIAGNOSTIC_CODES = new Set([
  "RUNTIME_PACK_INPUT_TOO_LARGE",
  "RUNTIME_PACK_INPUT_UTF8_INVALID",
  "RUNTIME_RECEIPT_INPUT_TOO_LARGE",
  "RUNTIME_RECEIPT_INPUT_UTF8_INVALID",
  "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
  "RUNTIME_PACK_JSON_DUPLICATE_KEY",
  "RUNTIME_PACK_JSON_INPUT_TYPE",
  "RUNTIME_PACK_JSON_NON_CANONICAL",
  "RUNTIME_PACK_JSON_SYNTAX",
  "RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED",
  "RUNTIME_RECEIPT_JSON_DUPLICATE_KEY",
  "RUNTIME_RECEIPT_JSON_INPUT_TYPE",
  "RUNTIME_RECEIPT_JSON_NON_CANONICAL",
  "RUNTIME_RECEIPT_JSON_SYNTAX",
  "RUNTIME_PACK_ACTION_ID_DUPLICATE",
  "RUNTIME_PACK_CONDITION_DEPTH_EXCEEDED",
  "RUNTIME_PACK_CONDITION_VALUE_TYPE_MISMATCH",
  "RUNTIME_PACK_CONDITION_VARIABLE_TYPE_MISMATCH",
  "RUNTIME_PACK_CUE_INDEX_INVALID",
  "RUNTIME_PACK_EFFECT_VALUE_TYPE_MISMATCH",
  "RUNTIME_PACK_EFFECT_VARIABLE_TYPE_MISMATCH",
  "RUNTIME_PACK_ENTITY_INDEX_INVALID",
  "RUNTIME_PACK_ENTRY_NODE_INDEX_INVALID",
  "RUNTIME_PACK_ENUM_INITIAL_NOT_ALLOWED",
  "RUNTIME_PACK_ENUM_VALUE_NOT_ALLOWED",
  "RUNTIME_PACK_NODE_NO_ENDING_PATH",
  "RUNTIME_PACK_NODE_UNREACHABLE",
  "RUNTIME_PACK_TARGET_INDEX_INVALID",
  "RUNTIME_PACK_TOP_LEVEL_ID_DUPLICATE",
  "RUNTIME_PACK_VARIABLE_INDEX_INVALID",
  "RUNTIME_PACK_SCHEMA_CONST",
  "RUNTIME_PACK_SCHEMA_DUPLICATE_ITEM",
  "RUNTIME_PACK_SCHEMA_ENUM",
  "RUNTIME_PACK_SCHEMA_FORBIDDEN_VALUE",
  "RUNTIME_PACK_SCHEMA_INVALID",
  "RUNTIME_PACK_SCHEMA_MAX_ITEMS",
  "RUNTIME_PACK_SCHEMA_MIN_ITEMS",
  "RUNTIME_PACK_SCHEMA_NUMBER_CONSTRAINT",
  "RUNTIME_PACK_SCHEMA_REQUIRED",
  "RUNTIME_PACK_SCHEMA_SHAPE",
  "RUNTIME_PACK_SCHEMA_STRING_CONSTRAINT",
  "RUNTIME_PACK_SCHEMA_TYPE",
  "RUNTIME_PACK_SCHEMA_UNKNOWN_PROPERTY",
  "RUNTIME_RECEIPT_SCHEMA_CONST",
  "RUNTIME_RECEIPT_SCHEMA_DUPLICATE_ITEM",
  "RUNTIME_RECEIPT_SCHEMA_ENUM",
  "RUNTIME_RECEIPT_SCHEMA_FORBIDDEN_VALUE",
  "RUNTIME_RECEIPT_SCHEMA_INVALID",
  "RUNTIME_RECEIPT_SCHEMA_MAX_ITEMS",
  "RUNTIME_RECEIPT_SCHEMA_MIN_ITEMS",
  "RUNTIME_RECEIPT_SCHEMA_NUMBER_CONSTRAINT",
  "RUNTIME_RECEIPT_SCHEMA_REQUIRED",
  "RUNTIME_RECEIPT_SCHEMA_SHAPE",
  "RUNTIME_RECEIPT_SCHEMA_STRING_CONSTRAINT",
  "RUNTIME_RECEIPT_SCHEMA_TYPE",
  "RUNTIME_RECEIPT_SCHEMA_UNKNOWN_PROPERTY",
  "RUNTIME_RECEIPT_ARTIFACT_BYTE_LENGTH_MISMATCH",
  "RUNTIME_RECEIPT_ARTIFACT_SHA256_MISMATCH",
]);

const REPORT_KEYS = Object.freeze(["reportVersion", "valid", "diagnostics"]);
const DIAGNOSTIC_REQUIRED_KEYS = Object.freeze([
  "phase",
  "severity",
  "code",
  "path",
  "message",
]);
const DIAGNOSTIC_ALLOWED_KEYS = new Set([
  ...DIAGNOSTIC_REQUIRED_KEYS,
  "relatedPath",
  "location",
]);
const LOCATION_KEYS = Object.freeze(["line", "column"]);
const RECEIPT_KEYS = Object.freeze([
  "format",
  "formatVersion",
  "canonicalization",
  "compiler",
  "artifact",
]);
const COMPILER_KEYS = Object.freeze(["id", "version"]);
const ARTIFACT_KEYS = Object.freeze([
  "format",
  "formatVersion",
  "sha256",
  "byteLength",
]);
const COMPILE_SUCCESS_KEYS = Object.freeze([
  "ok",
  "runtimePack",
  "canonicalJson",
  "receipt",
]);
const COMPILE_INVALID_KEYS = Object.freeze(["ok", "validationReport"]);

export class RuntimePackCliOperationalError extends Error {
  constructor(code) {
    super(code);
    this.name = "RuntimePackCliOperationalError";
    this.code = code;
  }
}

function fail(code) {
  throw new RuntimePackCliOperationalError(code);
}

function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative === "" ||
    (!path.isAbsolute(relative) &&
      relative !== ".." &&
      !relative.startsWith(`..${path.sep}`))
  );
}

function isDirectChild(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative !== "" &&
    !path.isAbsolute(relative) &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !relative.includes(path.sep)
  );
}

function samePath(left, right) {
  const resolvedLeft = path.resolve(left);
  const resolvedRight = path.resolve(right);
  return process.platform === "win32"
    ? resolvedLeft.toLowerCase() === resolvedRight.toLowerCase()
    : resolvedLeft === resolvedRight;
}

function dataRecord(value, expectedKeys) {
  if (value === null || typeof value !== "object") {
    return null;
  }
  let prototype;
  let descriptors;
  let keys;
  try {
    prototype = Object.getPrototypeOf(value);
    descriptors = Object.getOwnPropertyDescriptors(value);
    keys = Reflect.ownKeys(descriptors);
  } catch {
    return null;
  }
  if (
    prototype !== Object.prototype &&
    prototype !== null
  ) {
    return null;
  }
  if (
    keys.length !== expectedKeys.length ||
    keys.some(
      (key) =>
        typeof key !== "string" ||
        !expectedKeys.includes(key) ||
        !("value" in descriptors[key]) ||
        descriptors[key].enumerable !== true,
    )
  ) {
    return null;
  }
  return Object.fromEntries(
    expectedKeys.map((key) => [key, descriptors[key].value]),
  );
}

function safeErrorCode(error) {
  if (error === null || (typeof error !== "object" && typeof error !== "function")) {
    return undefined;
  }
  try {
    const descriptor = Object.getOwnPropertyDescriptor(error, "code");
    return descriptor && "value" in descriptor && typeof descriptor.value === "string"
      ? descriptor.value
      : undefined;
  } catch {
    return undefined;
  }
}

function isMissingError(error) {
  return safeErrorCode(error) === "ENOENT";
}

function exceedsByteLimit(size, maxBytes) {
  if (typeof size === "bigint") {
    return size > BigInt(maxBytes);
  }
  return Number.isFinite(size) && size > maxBytes;
}

function normalizeInputPath(candidate, codePrefix) {
  if (typeof candidate !== "string" || candidate.length === 0) {
    fail(`${codePrefix}_PATH_INVALID`);
  }
  if (candidate.includes("\0")) {
    fail(`${codePrefix}_ARGUMENT_INVALID`);
  }
  const normalized = candidate.replaceAll("\\", "/");
  if (
    path.posix.isAbsolute(normalized) ||
    path.win32.isAbsolute(candidate) ||
    /^[A-Za-z]:/.test(candidate)
  ) {
    fail(`${codePrefix}_PATH_NOT_RELATIVE`);
  }
  const segments = normalized.split("/");
  if (segments.some((segment) => segment === "..")) {
    fail(`${codePrefix}_PATH_TRAVERSAL`);
  }
  if (
    segments.some((segment) => {
      const deviceBase = segment.split(".", 1)[0].toLowerCase();
      return (
        segment === "" ||
        segment === "." ||
        /[<>:"|?*\u0000-\u001f]/.test(segment) ||
        /[. ]$/.test(segment) ||
        WINDOWS_RESERVED_SLUGS.has(deviceBase)
      );
    }) ||
    path.posix.extname(normalized).toLowerCase() !== ".json"
  ) {
    fail(`${codePrefix}_PATH_INVALID`);
  }
  return normalized;
}

function normalizeSlug(candidate) {
  if (
    typeof candidate !== "string" ||
    !/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(candidate) ||
    WINDOWS_RESERVED_SLUGS.has(candidate)
  ) {
    fail("PACK_COMPILE_CLI_OUTPUT_INVALID");
  }
  return candidate;
}

export function parseCompilePackCliArgs(args) {
  if (!Array.isArray(args)) {
    fail("PACK_COMPILE_CLI_ARGUMENT_INVALID");
  }
  let input;
  let output;
  let json = false;
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (typeof argument !== "string" || argument.includes("\0")) {
      fail("PACK_COMPILE_CLI_ARGUMENT_INVALID");
    }
    if (argument === "--json") {
      if (json) {
        fail("PACK_COMPILE_CLI_ARGUMENT_INVALID");
      }
      json = true;
      continue;
    }
    if (argument === "--output") {
      if (output !== undefined || index + 1 >= args.length) {
        fail("PACK_COMPILE_CLI_OUTPUT_INVALID");
      }
      output = normalizeSlug(args[index + 1]);
      index += 1;
      continue;
    }
    if (argument.startsWith("-")) {
      fail("PACK_COMPILE_CLI_UNKNOWN_OPTION");
    }
    if (input !== undefined) {
      fail("PACK_COMPILE_CLI_MULTIPLE_INPUTS");
    }
    input = normalizeInputPath(argument, "PACK_COMPILE_CLI");
  }
  if (input === undefined) {
    fail("PACK_COMPILE_CLI_INPUT_REQUIRED");
  }
  if (output === undefined) {
    fail("PACK_COMPILE_CLI_OUTPUT_REQUIRED");
  }
  return { input, output, json };
}

export function parseRuntimePackCliArgs(args) {
  if (!Array.isArray(args)) {
    fail("RUNTIME_PACK_CLI_ARGUMENT_INVALID");
  }
  const inputs = [];
  let json = false;
  for (const argument of args) {
    if (typeof argument !== "string" || argument.includes("\0")) {
      fail("RUNTIME_PACK_CLI_ARGUMENT_INVALID");
    }
    if (argument === "--json") {
      if (json) {
        fail("RUNTIME_PACK_CLI_ARGUMENT_INVALID");
      }
      json = true;
      continue;
    }
    if (argument.startsWith("-")) {
      fail("RUNTIME_PACK_CLI_UNKNOWN_OPTION");
    }
    inputs.push(normalizeInputPath(argument, "RUNTIME_PACK_CLI"));
  }
  if (inputs.length !== 2) {
    fail("RUNTIME_PACK_CLI_INPUTS_REQUIRED");
  }
  return { runtimeInput: inputs[0], receiptInput: inputs[1], json };
}

function normalizeLocation(location) {
  const record = dataRecord(location, LOCATION_KEYS);
  if (
    record === null ||
    !Number.isSafeInteger(record.line) ||
    record.line < 1 ||
    !Number.isSafeInteger(record.column) ||
    record.column < 1
  ) {
    return null;
  }
  return { line: record.line, column: record.column };
}

function normalizePointer(value) {
  if (typeof value !== "string" || value.length > 4096) {
    return null;
  }
  if (value === "") {
    return value;
  }
  if (!value.startsWith("/")) {
    return null;
  }
  const tokens = value.slice(1).split("/");
  if (
    tokens.some(
      (token) =>
        !(
          /^(?:0|[1-9][0-9]*)$/.test(token) ||
          PUBLIC_POINTER_TOKENS.has(token)
        ),
    )
  ) {
    return null;
  }
  return value;
}

function normalizeDiagnostic(diagnostic, allowedCodes, allowedPhases) {
  if (diagnostic === null || typeof diagnostic !== "object") {
    return null;
  }
  let descriptors;
  let keys;
  let prototype;
  try {
    prototype = Object.getPrototypeOf(diagnostic);
    descriptors = Object.getOwnPropertyDescriptors(diagnostic);
    keys = Reflect.ownKeys(descriptors);
  } catch {
    return null;
  }
  if (
    (prototype !== Object.prototype && prototype !== null) ||
    keys.some(
      (key) =>
        typeof key !== "string" ||
        !DIAGNOSTIC_ALLOWED_KEYS.has(key) ||
        !("value" in descriptors[key]) ||
        descriptors[key].enumerable !== true,
    ) ||
    DIAGNOSTIC_REQUIRED_KEYS.some(
      (key) => !Object.prototype.hasOwnProperty.call(descriptors, key),
    )
  ) {
    return null;
  }
  const phase = descriptors.phase.value;
  const severity = descriptors.severity.value;
  const code = descriptors.code.value;
  const pointer = normalizePointer(descriptors.path.value);
  if (
    !allowedPhases.has(phase) ||
    severity !== "error" ||
    typeof code !== "string" ||
    !allowedCodes.has(code) ||
    pointer === null
  ) {
    return null;
  }
  const normalized = {
    phase,
    severity: "error",
    code,
    path: pointer,
    message: code,
  };
  if (Object.prototype.hasOwnProperty.call(descriptors, "relatedPath")) {
    const relatedPath = normalizePointer(descriptors.relatedPath.value);
    if (relatedPath === null) {
      return null;
    }
    normalized.relatedPath = relatedPath;
  }
  if (Object.prototype.hasOwnProperty.call(descriptors, "location")) {
    const location = normalizeLocation(descriptors.location.value);
    if (location === null) {
      return null;
    }
    normalized.location = location;
  }
  return normalized;
}

function normalizeValidationReport(report, allowedCodes, allowedPhases) {
  const record = dataRecord(report, REPORT_KEYS);
  if (
    record === null ||
    record.reportVersion !== 1 ||
    typeof record.valid !== "boolean" ||
    !Array.isArray(record.diagnostics) ||
    record.valid !== (record.diagnostics.length === 0)
  ) {
    return null;
  }
  const diagnostics = [];
  for (const diagnostic of record.diagnostics) {
    const normalized = normalizeDiagnostic(
      diagnostic,
      allowedCodes,
      allowedPhases,
    );
    if (normalized === null) {
      return null;
    }
    diagnostics.push(normalized);
  }
  return { reportVersion: 1, valid: record.valid, diagnostics };
}

function inputDiagnostic(phase, code, pointer = "") {
  return {
    reportVersion: 1,
    valid: false,
    diagnostics: [
      { phase, severity: "error", code, path: pointer, message: code },
    ],
  };
}

function renderInvalid(report, json, label) {
  if (json) {
    return {
      exitCode: 1,
      stdout: `${JSON.stringify(report)}\n`,
      stderr: "",
      report,
    };
  }
  const lines = report.diagnostics.map((diagnostic) => {
    const pointer = diagnostic.path === "" ? "/" : diagnostic.path;
    return `${label} ${diagnostic.phase} ${diagnostic.code} ${pointer}`;
  });
  return { exitCode: 1, stdout: "", stderr: `${lines.join("\n")}\n`, report };
}

function operationalResult(code) {
  return { exitCode: 2, stdout: "", stderr: `${code}\n` };
}

function validRuntimeResult(json) {
  const report = { reportVersion: 1, valid: true, diagnostics: [] };
  if (json) {
    return {
      exitCode: 0,
      stdout: `${JSON.stringify(report)}\n`,
      stderr: "",
      report,
    };
  }
  return { exitCode: 0, stdout: "RUNTIME_PACK_VALID\n", stderr: "", report };
}

async function resolveModuleRoot(moduleRoot, realpath) {
  if (typeof moduleRoot !== "string") {
    fail("RUNTIME_PACK_CLI_INTERNAL_ERROR");
  }
  return realpath(moduleRoot);
}

async function readContainedJson({
  moduleRoot,
  input,
  maxBytes,
  tooLargeCode,
  invalidUtf8Code,
  diagnosticPath,
  ioCode,
  outsideCode,
  notFileCode,
  readFile,
  realpath,
  stat,
}) {
  const lexicalCandidate = path.resolve(moduleRoot, ...input.split("/"));
  if (!isInside(moduleRoot, lexicalCandidate)) {
    return { operationalCode: outsideCode };
  }
  let resolvedCandidate;
  let before;
  try {
    resolvedCandidate = await realpath(lexicalCandidate);
    if (!isInside(moduleRoot, resolvedCandidate)) {
      return { operationalCode: outsideCode };
    }
    if (path.extname(resolvedCandidate).toLowerCase() !== ".json") {
      return { operationalCode: notFileCode };
    }
    before = await stat(resolvedCandidate, { bigint: true });
  } catch {
    return { operationalCode: ioCode };
  }
  if (typeof before?.isFile !== "function" || !before.isFile()) {
    return { operationalCode: notFileCode };
  }
  if (exceedsByteLimit(before.size, maxBytes)) {
    return {
      report: inputDiagnostic("parse", tooLargeCode, diagnosticPath),
    };
  }
  let bytes;
  try {
    bytes = await readFile(resolvedCandidate);
  } catch {
    return { operationalCode: ioCode };
  }
  if (!(bytes instanceof Uint8Array)) {
    return { operationalCode: "RUNTIME_PACK_CLI_INTERNAL_ERROR" };
  }
  if (bytes.byteLength > maxBytes) {
    return {
      report: inputDiagnostic("parse", tooLargeCode, diagnosticPath),
    };
  }
  try {
    const afterTarget = await realpath(lexicalCandidate);
    const after = await stat(resolvedCandidate, { bigint: true });
    const beforeIdentity = statIdentity(before);
    const afterIdentity = statIdentity(after);
    if (
      !samePath(afterTarget, resolvedCandidate) ||
      typeof after?.isFile !== "function" ||
      !after.isFile() ||
      beforeIdentity === null ||
      afterIdentity === null ||
      beforeIdentity.dev !== afterIdentity.dev ||
      beforeIdentity.ino !== afterIdentity.ino
    ) {
      return { operationalCode: ioCode };
    }
  } catch {
    return { operationalCode: ioCode };
  }
  try {
    return { text: new TextDecoder("utf-8", { fatal: true }).decode(bytes) };
  } catch {
    return {
      report: inputDiagnostic("parse", invalidUtf8Code, diagnosticPath),
    };
  }
}

function normalizeReceipt(receipt) {
  const root = dataRecord(receipt, RECEIPT_KEYS);
  if (root === null) {
    return null;
  }
  const compiler = dataRecord(root.compiler, COMPILER_KEYS);
  const artifact = dataRecord(root.artifact, ARTIFACT_KEYS);
  if (
    compiler === null ||
    artifact === null ||
    root.format !== RECEIPT_FORMAT ||
    root.formatVersion !== RECEIPT_FORMAT_VERSION ||
    root.canonicalization !== CANONICALIZATION_PROFILE ||
    compiler.id !== COMPILER_ID ||
    compiler.version !== COMPILER_VERSION ||
    artifact.format !== RUNTIME_PACK_FORMAT ||
    artifact.formatVersion !== RUNTIME_PACK_FORMAT_VERSION ||
    typeof artifact.sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(artifact.sha256) ||
    !Number.isSafeInteger(artifact.byteLength) ||
    artifact.byteLength < 1
  ) {
    return null;
  }
  return {
    format: root.format,
    formatVersion: root.formatVersion,
    canonicalization: root.canonicalization,
    compiler: { id: compiler.id, version: compiler.version },
    artifact: {
      format: artifact.format,
      formatVersion: artifact.formatVersion,
      sha256: artifact.sha256,
      byteLength: artifact.byteLength,
    },
  };
}

function normalizeCompileResult(result, canonicalizeJsonValue) {
  if (result === null || typeof result !== "object") {
    return null;
  }
  let ok;
  try {
    const descriptor = Object.getOwnPropertyDescriptor(result, "ok");
    if (!descriptor || !("value" in descriptor)) {
      return null;
    }
    ok = descriptor.value;
  } catch {
    return null;
  }
  if (ok === false) {
    const record = dataRecord(result, COMPILE_INVALID_KEYS);
    if (record === null) {
      return null;
    }
    const validationReport = normalizeValidationReport(
      record.validationReport,
      AUTHORING_DIAGNOSTIC_CODES,
      new Set(["parse", "schema", "semantic"]),
    );
    return validationReport === null
      ? null
      : { ok: false, validationReport };
  }
  if (ok !== true) {
    return null;
  }
  const record = dataRecord(result, COMPILE_SUCCESS_KEYS);
  if (
    record === null ||
    typeof record.canonicalJson !== "string" ||
    record.canonicalJson.length === 0
  ) {
    return null;
  }
  const receipt = normalizeReceipt(record.receipt);
  if (receipt === null) {
    return null;
  }
  let canonicalRuntime;
  let canonicalReceipt;
  try {
    canonicalRuntime = canonicalizeJsonValue(record.runtimePack);
    canonicalReceipt = canonicalizeJsonValue(receipt);
  } catch {
    return null;
  }
  if (
    canonicalRuntime !== record.canonicalJson ||
    receipt.artifact.byteLength !==
      new TextEncoder().encode(canonicalRuntime).byteLength ||
    canonicalRuntime.startsWith("\uFEFF") ||
    canonicalRuntime.endsWith("\n") ||
    canonicalRuntime.endsWith("\r") ||
    canonicalReceipt.startsWith("\uFEFF") ||
    canonicalReceipt.endsWith("\n") ||
    canonicalReceipt.endsWith("\r")
  ) {
    return null;
  }
  return {
    ok: true,
    runtimePack: record.runtimePack,
    canonicalJson: canonicalRuntime,
    receipt,
    canonicalReceipt,
  };
}

function compileSuccessResult(json, receipt) {
  if (!json) {
    return { exitCode: 0, stdout: "PACK_COMPILED\n", stderr: "" };
  }
  const output = {
    resultVersion: 1,
    ok: true,
    files: [RUNTIME_PACK_FILE_NAME, RUNTIME_PACK_RECEIPT_FILE_NAME],
    receipt,
  };
  return {
    exitCode: 0,
    stdout: `${JSON.stringify(output)}\n`,
    stderr: "",
    receipt,
  };
}

function isDirectoryStat(value) {
  return typeof value?.isDirectory === "function" && value.isDirectory();
}

function isNonLinkDirectoryStat(value) {
  return (
    isDirectoryStat(value) &&
    typeof value?.isSymbolicLink === "function" &&
    !value.isSymbolicLink()
  );
}

function isNonLinkFileStat(value) {
  return (
    typeof value?.isFile === "function" &&
    value.isFile() &&
    typeof value?.isSymbolicLink === "function" &&
    !value.isSymbolicLink()
  );
}

function statIdentity(value) {
  if (
    typeof value?.dev === "bigint" &&
    typeof value?.ino === "bigint" &&
    value.ino !== 0n
  ) {
    return { dev: value.dev, ino: value.ino };
  }
  if (
    Number.isSafeInteger(value?.dev) &&
    Number.isSafeInteger(value?.ino) &&
    value.ino !== 0
  ) {
    return { dev: value.dev, ino: value.ino };
  }
  return null;
}

async function targetExists(target, lstat) {
  try {
    await lstat(target);
    return true;
  } catch (error) {
    if (isMissingError(error)) {
      return false;
    }
    throw error;
  }
}

async function prepareExportsRoot({ moduleRoot, mkdir, realpath, lstat }) {
  const lexicalExports = path.resolve(moduleRoot, "exports");
  if (!isInside(moduleRoot, lexicalExports)) {
    throw new Error("EXPORTS_OUTSIDE");
  }
  try {
    await mkdir(lexicalExports, { recursive: false });
  } catch (error) {
    if (safeErrorCode(error) !== "EEXIST") {
      throw error;
    }
  }
  const resolvedExports = await realpath(lexicalExports);
  const metadata = await lstat(lexicalExports);
  if (
    !samePath(lexicalExports, resolvedExports) ||
    !isInside(moduleRoot, resolvedExports) ||
    !isNonLinkDirectoryStat(metadata)
  ) {
    throw new Error("EXPORTS_UNSAFE");
  }
  return resolvedExports;
}

async function cleanupStaging({
  stagingPath,
  stagingIdentity,
  exportsRoot,
  lstat,
  realpath,
  rm,
}) {
  if (typeof stagingPath !== "string" || stagingIdentity === null) {
    return;
  }
  try {
    if (!isDirectChild(exportsRoot, stagingPath)) {
      return;
    }
    const basename = path.basename(stagingPath);
    if (!basename.startsWith(".matrix-oasis-")) {
      return;
    }
    const lexicalMetadata = await lstat(stagingPath, { bigint: true });
    const currentIdentity = statIdentity(lexicalMetadata);
    const resolved = await realpath(stagingPath);
    if (
      currentIdentity === null ||
      currentIdentity.dev !== stagingIdentity.dev ||
      currentIdentity.ino !== stagingIdentity.ino ||
      !samePath(stagingPath, resolved) ||
      !isDirectChild(exportsRoot, resolved) ||
      !isNonLinkDirectoryStat(lexicalMetadata)
    ) {
      return;
    }
    await rm(resolved, {
      recursive: true,
      force: false,
      maxRetries: process.platform === "win32" ? 2 : 0,
      retryDelay: 25,
    });
  } catch {
    // Ambiguous cleanup is deliberately abandoned instead of widening deletion.
  }
}

async function assertTrustedDirectory({
  candidate,
  exportsRoot,
  expectedIdentity,
  lstat,
  realpath,
}) {
  if (!isDirectChild(exportsRoot, candidate) || expectedIdentity === null) {
    throw new Error("DIRECTORY_UNTRUSTED");
  }
  const metadata = await lstat(candidate, { bigint: true });
  const identity = statIdentity(metadata);
  const resolved = await realpath(candidate);
  if (
    identity === null ||
    identity.dev !== expectedIdentity.dev ||
    identity.ino !== expectedIdentity.ino ||
    !isNonLinkDirectoryStat(metadata) ||
    !samePath(candidate, resolved) ||
    !isDirectChild(exportsRoot, resolved)
  ) {
    throw new Error("DIRECTORY_UNTRUSTED");
  }
}

async function assertTrustedFilePath({
  candidate,
  parent,
  expectedIdentity,
  lstat,
  realpath,
}) {
  if (!isDirectChild(parent, candidate) || expectedIdentity === null) {
    throw new Error("FILE_UNTRUSTED");
  }
  const metadata = await lstat(candidate, { bigint: true });
  const identity = statIdentity(metadata);
  const resolved = await realpath(candidate);
  if (
    identity === null ||
    identity.dev !== expectedIdentity.dev ||
    identity.ino !== expectedIdentity.ino ||
    !isNonLinkFileStat(metadata) ||
    !samePath(candidate, resolved) ||
    !isDirectChild(parent, resolved)
  ) {
    throw new Error("FILE_UNTRUSTED");
  }
}

async function assertTrustedFileHandle({
  handle,
  candidate,
  parent,
  expectedIdentity,
  lstat,
  realpath,
}) {
  if (
    !handle ||
    typeof handle.stat !== "function" ||
    typeof handle.writeFile !== "function" ||
    typeof handle.sync !== "function" ||
    typeof handle.read !== "function" ||
    typeof handle.close !== "function"
  ) {
    throw new Error("FILE_HANDLE_UNTRUSTED");
  }
  const handleMetadata = await handle.stat({ bigint: true });
  const handleIdentity = statIdentity(handleMetadata);
  if (
    handleIdentity === null ||
    typeof handleMetadata?.isFile !== "function" ||
    !handleMetadata.isFile() ||
    (expectedIdentity !== null &&
      (handleIdentity.dev !== expectedIdentity.dev ||
        handleIdentity.ino !== expectedIdentity.ino))
  ) {
    throw new Error("FILE_HANDLE_UNTRUSTED");
  }
  await assertTrustedFilePath({
    candidate,
    parent,
    expectedIdentity: handleIdentity,
    lstat,
    realpath,
  });
  return handleIdentity;
}

async function readHandleExact(handle, expectedBytes) {
  const bytes = new Uint8Array(expectedBytes.byteLength);
  let offset = 0;
  while (offset < bytes.byteLength) {
    const result = await handle.read(
      bytes,
      offset,
      bytes.byteLength - offset,
      offset,
    );
    if (!result || !Number.isSafeInteger(result.bytesRead) || result.bytesRead < 1) {
      throw new Error("HANDLE_READBACK_INVALID");
    }
    if (result.bytesRead > bytes.byteLength - offset) {
      throw new Error("HANDLE_READBACK_INVALID");
    }
    offset += result.bytesRead;
  }
  const trailing = new Uint8Array(1);
  const tail = await handle.read(trailing, 0, 1, expectedBytes.byteLength);
  if (!tail || tail.bytesRead !== 0) {
    throw new Error("HANDLE_READBACK_INVALID");
  }
  return bytes;
}

function bytesEqual(left, right) {
  if (left.byteLength !== right.byteLength) {
    return false;
  }
  for (let index = 0; index < left.byteLength; index += 1) {
    if (left[index] !== right[index]) {
      return false;
    }
  }
  return true;
}

async function validatePublishedTexts({
  runtimeText,
  receiptText,
  validateRuntimeGamePackJson,
}) {
  const report = await validateRuntimeGamePackJson(runtimeText, receiptText);
  const normalizedReport = normalizeValidationReport(
    report,
    RUNTIME_DIAGNOSTIC_CODES,
    new Set(["parse", "schema", "semantic", "integrity"]),
  );
  if (normalizedReport === null || !normalizedReport.valid) {
    throw new Error("SELF_VALIDATION_FAILED");
  }
}

async function publishCompiledPair({
  moduleRoot,
  outputSlug,
  runtimeText,
  receiptText,
  validateRuntimeGamePackJson,
  RuntimeGamePackValidatorOperationalError,
  openFile,
  mkdir,
  mkdtemp,
  rename,
  rm,
  realpath,
  lstat,
}) {
  let exportsRoot;
  try {
    exportsRoot = await prepareExportsRoot({
      moduleRoot,
      mkdir,
      realpath,
      lstat,
    });
  } catch (error) {
    if (
      typeof RuntimeGamePackValidatorOperationalError === "function" &&
      error instanceof RuntimeGamePackValidatorOperationalError &&
      safeErrorCode(error) === "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR"
    ) {
      return { ok: false, code: "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR" };
    }
    return { ok: false, code: "PACK_COMPILE_CLI_IO_ERROR" };
  }
  const target = path.resolve(exportsRoot, outputSlug);
  try {
    if (!isDirectChild(exportsRoot, target) || (await targetExists(target, lstat))) {
      return { ok: false, code: "PACK_COMPILE_CLI_OUTPUT_EXISTS" };
    }
  } catch {
    return { ok: false, code: "PACK_COMPILE_CLI_IO_ERROR" };
  }

  let stagingPath;
  let stagingIdentity = null;
  let runtimeHandle;
  let receiptHandle;
  try {
    const prefix = path.join(exportsRoot, `.matrix-oasis-${outputSlug}-`);
    stagingPath = await mkdtemp(prefix);
    const stagingName = path.basename(stagingPath);
    const resolvedStaging = await realpath(stagingPath);
    const stagingMetadata = await lstat(stagingPath, { bigint: true });
    stagingIdentity = statIdentity(stagingMetadata);
    if (
      !stagingName.startsWith(`.matrix-oasis-${outputSlug}-`) ||
      !isDirectChild(exportsRoot, stagingPath) ||
      !samePath(stagingPath, resolvedStaging) ||
      !isNonLinkDirectoryStat(stagingMetadata) ||
      stagingIdentity === null
    ) {
      throw new Error("STAGING_UNSAFE");
    }
    const runtimePath = path.join(stagingPath, RUNTIME_PACK_FILE_NAME);
    const receiptPath = path.join(stagingPath, RUNTIME_PACK_RECEIPT_FILE_NAME);
    const trustStaging = () =>
      assertTrustedDirectory({
        candidate: stagingPath,
        exportsRoot,
        expectedIdentity: stagingIdentity,
        lstat,
        realpath,
      });

    await trustStaging();
    runtimeHandle = await openFile(runtimePath, "wx+");
    await trustStaging();
    const runtimeIdentity = await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: runtimePath,
      parent: stagingPath,
      expectedIdentity: null,
      lstat,
      realpath,
    });

    await trustStaging();
    receiptHandle = await openFile(receiptPath, "wx+");
    await trustStaging();
    const receiptIdentity = await assertTrustedFileHandle({
      handle: receiptHandle,
      candidate: receiptPath,
      parent: stagingPath,
      expectedIdentity: null,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: runtimePath,
      parent: stagingPath,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });

    const runtimeBytes = new TextEncoder().encode(runtimeText);
    const receiptBytes = new TextEncoder().encode(receiptText);
    await runtimeHandle.writeFile(runtimeBytes);
    await runtimeHandle.sync();
    await trustStaging();
    await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: runtimePath,
      parent: stagingPath,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: receiptHandle,
      candidate: receiptPath,
      parent: stagingPath,
      expectedIdentity: receiptIdentity,
      lstat,
      realpath,
    });

    await receiptHandle.writeFile(receiptBytes);
    await receiptHandle.sync();
    await trustStaging();
    await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: runtimePath,
      parent: stagingPath,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: receiptHandle,
      candidate: receiptPath,
      parent: stagingPath,
      expectedIdentity: receiptIdentity,
      lstat,
      realpath,
    });

    const runtimeReadback = await readHandleExact(runtimeHandle, runtimeBytes);
    const receiptReadback = await readHandleExact(receiptHandle, receiptBytes);
    if (
      !bytesEqual(runtimeReadback, runtimeBytes) ||
      !bytesEqual(receiptReadback, receiptBytes)
    ) {
      throw new Error("HANDLE_READBACK_MISMATCH");
    }
    await trustStaging();
    await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: runtimePath,
      parent: stagingPath,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: receiptHandle,
      candidate: receiptPath,
      parent: stagingPath,
      expectedIdentity: receiptIdentity,
      lstat,
      realpath,
    });
    await validatePublishedTexts({
      runtimeText,
      receiptText,
      validateRuntimeGamePackJson,
    });

    await runtimeHandle.close();
    runtimeHandle = undefined;
    await receiptHandle.close();
    receiptHandle = undefined;
    await trustStaging();
    await assertTrustedFilePath({
      candidate: runtimePath,
      parent: stagingPath,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFilePath({
      candidate: receiptPath,
      parent: stagingPath,
      expectedIdentity: receiptIdentity,
      lstat,
      realpath,
    });

    await trustStaging();
    if (await targetExists(target, lstat)) {
      return { ok: false, code: "PACK_COMPILE_CLI_OUTPUT_EXISTS" };
    }
    await trustStaging();
    await rename(stagingPath, target);
    await assertTrustedDirectory({
      candidate: target,
      exportsRoot,
      expectedIdentity: stagingIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFilePath({
      candidate: path.join(target, RUNTIME_PACK_FILE_NAME),
      parent: target,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFilePath({
      candidate: path.join(target, RUNTIME_PACK_RECEIPT_FILE_NAME),
      parent: target,
      expectedIdentity: receiptIdentity,
      lstat,
      realpath,
    });

    const publishedRuntimePath = path.join(target, RUNTIME_PACK_FILE_NAME);
    const publishedReceiptPath = path.join(
      target,
      RUNTIME_PACK_RECEIPT_FILE_NAME,
    );
    runtimeHandle = await openFile(publishedRuntimePath, "r");
    await assertTrustedDirectory({
      candidate: target,
      exportsRoot,
      expectedIdentity: stagingIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: publishedRuntimePath,
      parent: target,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    receiptHandle = await openFile(publishedReceiptPath, "r");
    await assertTrustedDirectory({
      candidate: target,
      exportsRoot,
      expectedIdentity: stagingIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: receiptHandle,
      candidate: publishedReceiptPath,
      parent: target,
      expectedIdentity: receiptIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: publishedRuntimePath,
      parent: target,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    const finalRuntimeReadback = await readHandleExact(
      runtimeHandle,
      runtimeBytes,
    );
    const finalReceiptReadback = await readHandleExact(
      receiptHandle,
      receiptBytes,
    );
    if (
      !bytesEqual(finalRuntimeReadback, runtimeBytes) ||
      !bytesEqual(finalReceiptReadback, receiptBytes)
    ) {
      throw new Error("FINAL_READBACK_MISMATCH");
    }
    await validatePublishedTexts({
      runtimeText: new TextDecoder("utf-8", { fatal: true }).decode(
        finalRuntimeReadback,
      ),
      receiptText: new TextDecoder("utf-8", { fatal: true }).decode(
        finalReceiptReadback,
      ),
      validateRuntimeGamePackJson,
    });
    await assertTrustedDirectory({
      candidate: target,
      exportsRoot,
      expectedIdentity: stagingIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: runtimeHandle,
      candidate: publishedRuntimePath,
      parent: target,
      expectedIdentity: runtimeIdentity,
      lstat,
      realpath,
    });
    await assertTrustedFileHandle({
      handle: receiptHandle,
      candidate: publishedReceiptPath,
      parent: target,
      expectedIdentity: receiptIdentity,
      lstat,
      realpath,
    });
    await runtimeHandle.close();
    runtimeHandle = undefined;
    await receiptHandle.close();
    receiptHandle = undefined;
    stagingPath = undefined;
    stagingIdentity = null;
    return { ok: true };
  } catch (error) {
    if (
      typeof RuntimeGamePackValidatorOperationalError === "function" &&
      error instanceof RuntimeGamePackValidatorOperationalError &&
      safeErrorCode(error) === "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR"
    ) {
      return { ok: false, code: "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR" };
    }
    return { ok: false, code: "PACK_COMPILE_CLI_IO_ERROR" };
  } finally {
    for (const handle of [runtimeHandle, receiptHandle]) {
      try {
        await handle?.close();
      } catch {
        // Close failures remain a static operational failure from the try block.
      }
    }
    await cleanupStaging({
      stagingPath,
      stagingIdentity,
      exportsRoot,
      lstat,
      realpath,
      rm,
    });
  }
}

export async function executeCompilePackCli({
  args,
  moduleRoot,
  readFile,
  openFile,
  mkdir,
  mkdtemp,
  rename,
  rm,
  realpath,
  stat,
  lstat,
  compileAuthoringGamePackJson,
  GamePackCompilerOperationalError,
  canonicalizeJsonValue,
  validateRuntimeGamePackJson,
  RuntimeGamePackValidatorOperationalError,
  maxInputBytes = MAX_AUTHORING_PACK_BYTES,
}) {
  try {
    if (
      !Array.isArray(args) ||
      typeof readFile !== "function" ||
      typeof openFile !== "function" ||
      typeof mkdir !== "function" ||
      typeof mkdtemp !== "function" ||
      typeof rename !== "function" ||
      typeof rm !== "function" ||
      typeof realpath !== "function" ||
      typeof stat !== "function" ||
      typeof lstat !== "function" ||
      typeof compileAuthoringGamePackJson !== "function" ||
      typeof canonicalizeJsonValue !== "function" ||
      typeof validateRuntimeGamePackJson !== "function" ||
      !Number.isSafeInteger(maxInputBytes) ||
      maxInputBytes < 1
    ) {
      return operationalResult("PACK_COMPILE_CLI_INTERNAL_ERROR");
    }
    const parsed = parseCompilePackCliArgs(args);
    let resolvedRoot;
    try {
      resolvedRoot = await resolveModuleRoot(moduleRoot, realpath);
    } catch {
      return operationalResult("PACK_COMPILE_CLI_IO_ERROR");
    }
    const input = await readContainedJson({
      moduleRoot: resolvedRoot,
      input: parsed.input,
      maxBytes: maxInputBytes,
      tooLargeCode: "PACK_INPUT_TOO_LARGE",
      invalidUtf8Code: "PACK_INPUT_UTF8_INVALID",
      diagnosticPath: "",
      ioCode: "PACK_COMPILE_CLI_IO_ERROR",
      outsideCode: "PACK_COMPILE_CLI_PATH_OUTSIDE_MODULE",
      notFileCode: "PACK_COMPILE_CLI_INPUT_NOT_FILE",
      readFile,
      realpath,
      stat,
    });
    if (input.operationalCode) {
      return operationalResult(input.operationalCode);
    }
    if (input.report) {
      return renderInvalid(input.report, parsed.json, "PACK_COMPILE_INVALID");
    }

    let rawResult;
    try {
      rawResult = await compileAuthoringGamePackJson(input.text);
    } catch (error) {
      if (
        typeof GamePackCompilerOperationalError === "function" &&
        error instanceof GamePackCompilerOperationalError &&
        safeErrorCode(error) === "PACK_COMPILER_INTERNAL_ERROR"
      ) {
        return operationalResult("PACK_COMPILER_INTERNAL_ERROR");
      }
      return operationalResult("PACK_COMPILE_CLI_INTERNAL_ERROR");
    }
    const result = normalizeCompileResult(rawResult, canonicalizeJsonValue);
    if (result === null) {
      return operationalResult("PACK_COMPILE_CLI_INTERNAL_ERROR");
    }
    if (!result.ok) {
      return renderInvalid(
        result.validationReport,
        parsed.json,
        "PACK_COMPILE_INVALID",
      );
    }

    const publication = await publishCompiledPair({
      moduleRoot: resolvedRoot,
      outputSlug: parsed.output,
      runtimeText: result.canonicalJson,
      receiptText: result.canonicalReceipt,
      validateRuntimeGamePackJson,
      RuntimeGamePackValidatorOperationalError,
      openFile,
      mkdir,
      mkdtemp,
      rename,
      rm,
      realpath,
      lstat,
    });
    if (!publication.ok) {
      return operationalResult(publication.code);
    }
    return compileSuccessResult(parsed.json, result.receipt);
  } catch (error) {
    if (error instanceof RuntimePackCliOperationalError) {
      return operationalResult(error.code);
    }
    if (
      typeof RuntimeGamePackValidatorOperationalError === "function" &&
      error instanceof RuntimeGamePackValidatorOperationalError
    ) {
      return operationalResult("RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR");
    }
    return operationalResult("PACK_COMPILE_CLI_INTERNAL_ERROR");
  }
}

export async function executeRuntimePackCli({
  args,
  moduleRoot,
  readFile,
  realpath,
  stat,
  validateRuntimeGamePackJson,
  RuntimeGamePackValidatorOperationalError,
  maxRuntimeBytes = MAX_RUNTIME_PACK_BYTES,
  maxReceiptBytes = MAX_RUNTIME_RECEIPT_BYTES,
}) {
  try {
    if (
      !Array.isArray(args) ||
      typeof readFile !== "function" ||
      typeof realpath !== "function" ||
      typeof stat !== "function" ||
      typeof validateRuntimeGamePackJson !== "function" ||
      !Number.isSafeInteger(maxRuntimeBytes) ||
      maxRuntimeBytes < 1 ||
      !Number.isSafeInteger(maxReceiptBytes) ||
      maxReceiptBytes < 1
    ) {
      return operationalResult("RUNTIME_PACK_CLI_INTERNAL_ERROR");
    }
    const parsed = parseRuntimePackCliArgs(args);
    let resolvedRoot;
    try {
      resolvedRoot = await resolveModuleRoot(moduleRoot, realpath);
    } catch {
      return operationalResult("RUNTIME_PACK_CLI_IO_ERROR");
    }
    const runtime = await readContainedJson({
      moduleRoot: resolvedRoot,
      input: parsed.runtimeInput,
      maxBytes: maxRuntimeBytes,
      tooLargeCode: "RUNTIME_PACK_INPUT_TOO_LARGE",
      invalidUtf8Code: "RUNTIME_PACK_INPUT_UTF8_INVALID",
      diagnosticPath: "/runtimePack",
      ioCode: "RUNTIME_PACK_CLI_IO_ERROR",
      outsideCode: "RUNTIME_PACK_CLI_PATH_OUTSIDE_MODULE",
      notFileCode: "RUNTIME_PACK_CLI_INPUT_NOT_FILE",
      readFile,
      realpath,
      stat,
    });
    if (runtime.operationalCode) {
      return operationalResult(runtime.operationalCode);
    }
    if (runtime.report) {
      return renderInvalid(runtime.report, parsed.json, "RUNTIME_PACK_INVALID");
    }
    const receipt = await readContainedJson({
      moduleRoot: resolvedRoot,
      input: parsed.receiptInput,
      maxBytes: maxReceiptBytes,
      tooLargeCode: "RUNTIME_RECEIPT_INPUT_TOO_LARGE",
      invalidUtf8Code: "RUNTIME_RECEIPT_INPUT_UTF8_INVALID",
      diagnosticPath: "/receipt",
      ioCode: "RUNTIME_PACK_CLI_IO_ERROR",
      outsideCode: "RUNTIME_PACK_CLI_PATH_OUTSIDE_MODULE",
      notFileCode: "RUNTIME_PACK_CLI_INPUT_NOT_FILE",
      readFile,
      realpath,
      stat,
    });
    if (receipt.operationalCode) {
      return operationalResult(receipt.operationalCode);
    }
    if (receipt.report) {
      return renderInvalid(receipt.report, parsed.json, "RUNTIME_PACK_INVALID");
    }

    let report;
    try {
      report = await validateRuntimeGamePackJson(runtime.text, receipt.text);
    } catch (error) {
      if (
        typeof RuntimeGamePackValidatorOperationalError === "function" &&
        error instanceof RuntimeGamePackValidatorOperationalError &&
        safeErrorCode(error) === "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR"
      ) {
        return operationalResult("RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR");
      }
      return operationalResult("RUNTIME_PACK_CLI_INTERNAL_ERROR");
    }
    const normalizedReport = normalizeValidationReport(
      report,
      RUNTIME_DIAGNOSTIC_CODES,
      new Set(["parse", "schema", "semantic", "integrity"]),
    );
    if (normalizedReport === null) {
      return operationalResult("RUNTIME_PACK_CLI_INTERNAL_ERROR");
    }
    return normalizedReport.valid
      ? validRuntimeResult(parsed.json)
      : renderInvalid(normalizedReport, parsed.json, "RUNTIME_PACK_INVALID");
  } catch (error) {
    if (error instanceof RuntimePackCliOperationalError) {
      return operationalResult(error.code);
    }
    return operationalResult("RUNTIME_PACK_CLI_INTERNAL_ERROR");
  }
}
