import path from "node:path";

export const MAX_PACK_BYTES = 1024 * 1024;

export class PackCliOperationalError extends Error {
  constructor(code) {
    super(code);
    this.name = "PackCliOperationalError";
    this.code = code;
  }
}

function fail(code) {
  throw new PackCliOperationalError(code);
}

function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative === "" ||
    (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`))
  );
}

function normalizeInputPath(candidate) {
  if (typeof candidate !== "string" || candidate.length === 0) {
    fail("PACK_CLI_PATH_INVALID");
  }
  if (candidate.includes("\0")) {
    fail("PACK_CLI_PATH_NUL");
  }

  const normalized = candidate.replaceAll("\\", "/");
  if (
    path.posix.isAbsolute(normalized) ||
    path.win32.isAbsolute(candidate) ||
    /^[A-Za-z]:/.test(candidate)
  ) {
    fail("PACK_CLI_PATH_NOT_RELATIVE");
  }

  const segments = normalized.split("/");
  if (segments.some((segment) => segment === "..")) {
    fail("PACK_CLI_PATH_TRAVERSAL");
  }
  if (path.posix.extname(normalized).toLowerCase() !== ".json") {
    fail("PACK_CLI_EXTENSION_INVALID");
  }

  return normalized;
}

export function parsePackCliArgs(args) {
  if (!Array.isArray(args)) {
    fail("PACK_CLI_ARGUMENT_INVALID");
  }

  let input;
  let json = false;
  for (const argument of args) {
    if (typeof argument !== "string" || argument.includes("\0")) {
      fail("PACK_CLI_ARGUMENT_INVALID");
    }
    if (argument === "--json") {
      if (json) {
        fail("PACK_CLI_ARGUMENT_INVALID");
      }
      json = true;
      continue;
    }
    if (argument.startsWith("-")) {
      fail("PACK_CLI_UNKNOWN_OPTION");
    }
    if (input !== undefined) {
      fail("PACK_CLI_MULTIPLE_INPUTS");
    }
    input = argument;
  }

  if (input === undefined) {
    fail("PACK_CLI_INPUT_REQUIRED");
  }
  return { input: normalizeInputPath(input), json };
}

function parseDiagnostic(code, message) {
  return {
    phase: "parse",
    severity: "error",
    code,
    path: "",
    message,
  };
}

function invalidInputReport(code, message) {
  return {
    reportVersion: 1,
    valid: false,
    diagnostics: [parseDiagnostic(code, message)],
  };
}

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

function hasExactKeys(value, expectedKeys) {
  const ownKeys = Reflect.ownKeys(value);
  return (
    ownKeys.length === expectedKeys.length &&
    ownKeys.every((key) =>
      typeof key === "string" && expectedKeys.includes(key)
    )
  );
}

function normalizeLocation(location) {
  if (
    location === null ||
    typeof location !== "object" ||
    !hasExactKeys(location, LOCATION_KEYS) ||
    !Number.isSafeInteger(location.line) ||
    location.line < 1 ||
    !Number.isSafeInteger(location.column) ||
    location.column < 1
  ) {
    return null;
  }
  return { line: location.line, column: location.column };
}

function normalizeDiagnostic(diagnostic) {
  if (diagnostic === null || typeof diagnostic !== "object") {
    return null;
  }
  const ownKeys = Reflect.ownKeys(diagnostic);
  if (
    ownKeys.some(
      (key) => typeof key !== "string" || !DIAGNOSTIC_ALLOWED_KEYS.has(key),
    ) ||
    DIAGNOSTIC_REQUIRED_KEYS.some(
      (key) => !Object.prototype.hasOwnProperty.call(diagnostic, key),
    ) ||
    !["parse", "schema", "semantic"].includes(diagnostic.phase) ||
    diagnostic.severity !== "error" ||
    typeof diagnostic.code !== "string" ||
    typeof diagnostic.path !== "string" ||
    typeof diagnostic.message !== "string"
  ) {
    return null;
  }

  const normalized = {
    phase: diagnostic.phase,
    severity: "error",
    code: diagnostic.code,
    path: diagnostic.path,
    message: diagnostic.message,
  };
  if (Object.prototype.hasOwnProperty.call(diagnostic, "relatedPath")) {
    if (typeof diagnostic.relatedPath !== "string") {
      return null;
    }
    normalized.relatedPath = diagnostic.relatedPath;
  }
  if (Object.prototype.hasOwnProperty.call(diagnostic, "location")) {
    const location = normalizeLocation(diagnostic.location);
    if (location === null) {
      return null;
    }
    normalized.location = location;
  }
  return normalized;
}

function normalizeValidationReport(report) {
  if (
    report === null ||
    typeof report !== "object" ||
    !hasExactKeys(report, REPORT_KEYS) ||
    report.reportVersion !== 1 ||
    typeof report.valid !== "boolean" ||
    !Array.isArray(report.diagnostics) ||
    report.valid !== (report.diagnostics.length === 0)
  ) {
    return null;
  }

  const diagnostics = [];
  for (const diagnostic of report.diagnostics) {
    const normalized = normalizeDiagnostic(diagnostic);
    if (normalized === null) {
      return null;
    }
    diagnostics.push(normalized);
  }
  return { reportVersion: 1, valid: report.valid, diagnostics };
}

function renderValidation(report, json) {
  if (json) {
    return {
      exitCode: report.valid ? 0 : 1,
      stdout: `${JSON.stringify(report)}\n`,
      stderr: "",
      report,
    };
  }

  if (report.valid) {
    return { exitCode: 0, stdout: "PACK_VALID\n", stderr: "", report };
  }

  const lines = report.diagnostics.map((diagnostic) => {
    const pointer = diagnostic.path === "" ? "/" : diagnostic.path;
    return `PACK_INVALID ${diagnostic.phase} ${diagnostic.code} ${pointer}`;
  });
  return {
    exitCode: 1,
    stdout: "",
    stderr: `${lines.join("\n")}\n`,
    report,
  };
}

function operationalResult(code) {
  return { exitCode: 2, stdout: "", stderr: `${code}\n` };
}

export async function executePackCli({
  args,
  moduleRoot,
  readFile,
  realpath,
  stat,
  validateAuthoringGamePackJson,
  AuthoringGamePackOperationalError,
  maxBytes = MAX_PACK_BYTES,
}) {
  try {
    if (
      typeof moduleRoot !== "string" ||
      typeof readFile !== "function" ||
      typeof realpath !== "function" ||
      typeof stat !== "function" ||
      typeof validateAuthoringGamePackJson !== "function" ||
      (AuthoringGamePackOperationalError !== undefined &&
        typeof AuthoringGamePackOperationalError !== "function") ||
      !Number.isSafeInteger(maxBytes) ||
      maxBytes < 1
    ) {
      return operationalResult("PACK_CLI_INTERNAL_ERROR");
    }

    const parsed = parsePackCliArgs(args);
    let resolvedRoot;
    let lexicalCandidate;
    let resolvedCandidate;
    let metadata;
    try {
      resolvedRoot = await realpath(moduleRoot);
      lexicalCandidate = path.resolve(resolvedRoot, ...parsed.input.split("/"));
      if (!isInside(resolvedRoot, lexicalCandidate)) {
        return operationalResult("PACK_CLI_PATH_OUTSIDE_MODULE");
      }
      resolvedCandidate = await realpath(lexicalCandidate);
      if (!isInside(resolvedRoot, resolvedCandidate)) {
        return operationalResult("PACK_CLI_PATH_OUTSIDE_MODULE");
      }
      if (path.extname(resolvedCandidate).toLowerCase() !== ".json") {
        return operationalResult("PACK_CLI_EXTENSION_INVALID");
      }
      metadata = await stat(resolvedCandidate);
    } catch {
      return operationalResult("PACK_CLI_IO_ERROR");
    }

    if (typeof metadata?.isFile !== "function" || !metadata.isFile()) {
      return operationalResult("PACK_CLI_INPUT_NOT_FILE");
    }

    if (Number.isFinite(metadata.size) && metadata.size > maxBytes) {
      return renderValidation(
        invalidInputReport(
          "PACK_INPUT_TOO_LARGE",
          "Input exceeds the 1 MiB limit.",
        ),
        parsed.json,
      );
    }

    let bytes;
    try {
      bytes = await readFile(resolvedCandidate);
    } catch {
      return operationalResult("PACK_CLI_IO_ERROR");
    }
    if (!(bytes instanceof Uint8Array)) {
      return operationalResult("PACK_CLI_INTERNAL_ERROR");
    }
    if (bytes.byteLength > maxBytes) {
      return renderValidation(
        invalidInputReport(
          "PACK_INPUT_TOO_LARGE",
          "Input exceeds the 1 MiB limit.",
        ),
        parsed.json,
      );
    }

    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      return renderValidation(
        invalidInputReport("PACK_INPUT_UTF8_INVALID", "Input must be valid UTF-8."),
        parsed.json,
      );
    }

    let report;
    try {
      report = validateAuthoringGamePackJson(text);
    } catch (error) {
      if (
        typeof AuthoringGamePackOperationalError === "function" &&
        error instanceof AuthoringGamePackOperationalError &&
        error.code === "PACK_VALIDATOR_INTERNAL_ERROR"
      ) {
        return operationalResult("PACK_VALIDATOR_INTERNAL_ERROR");
      }
      return operationalResult("PACK_CLI_INTERNAL_ERROR");
    }
    const normalizedReport = normalizeValidationReport(report);
    if (normalizedReport === null) {
      return operationalResult("PACK_CLI_INTERNAL_ERROR");
    }
    return renderValidation(normalizedReport, parsed.json);
  } catch (error) {
    if (error instanceof PackCliOperationalError) {
      return operationalResult(error.code);
    }
    return operationalResult("PACK_CLI_INTERNAL_ERROR");
  }
}
