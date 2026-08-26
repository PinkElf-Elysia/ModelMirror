import { spawnSync } from "node:child_process";
import path from "node:path";

export const GODOT_REQUIRED_VERSION = "4.6.3";
export const GODOT_READINESS_MARKER =
  "MATRIX_OASIS_R4_GODOT_FOUNDATION_READY";

export class GodotHarnessError extends Error {
  constructor(code, processFailure = null) {
    super(code);
    this.name = "GodotHarnessError";
    this.code = code;
    Object.defineProperty(this, "processFailure", {
      value: processFailure,
      enumerable: false,
      writable: false,
      configurable: false,
    });
  }
}

function fail(code, processFailure = null) {
  throw new GodotHarnessError(code, processFailure);
}

export function classifyGodotProcessFailure(result) {
  if (!result || typeof result !== "object") return "unknown";
  if (result.error?.code === "ETIMEDOUT") return "timeout";
  if (result.error?.code === "ENOBUFS") return "output-limit";
  if (result.error) return "spawn-error";
  if (typeof result.signal === "string" && result.signal) return "signal";
  if (result.status !== 0) return "nonzero-exit";
  return null;
}

export function extractGodotVersion(output) {
  return /(\d+\.\d+\.\d+)/.exec(output ?? "")?.[1] ?? null;
}

export function godotCandidates(environment = process.env) {
  const candidates = [];
  if (typeof environment.GODOT_BIN === "string" && environment.GODOT_BIN) {
    candidates.push(environment.GODOT_BIN);
  }
  candidates.push(process.platform === "win32" ? "godot.exe" : "godot", "godot4");
  return [...new Set(candidates)];
}

export function resolveGodotBinary({ environment = process.env, probe = spawnSync } = {}) {
  for (const candidate of godotCandidates(environment)) {
    const result = probe(candidate, ["--version"], {
      encoding: "utf8",
      shell: false,
      timeout: 5_000,
      windowsHide: true,
    });
    if (result.error || result.status !== 0) {
      continue;
    }
    const version = extractGodotVersion(`${result.stdout ?? ""} ${result.stderr ?? ""}`);
    if (version !== GODOT_REQUIRED_VERSION) {
      continue;
    }
    return { command: candidate, version };
  }
  fail("GODOT_4_6_3_NOT_AVAILABLE");
}

export function runGodotCommand({ command, args, cwd, timeout = 60_000, spawn = spawnSync }) {
  const result = spawn(command, args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 8 * 1024 * 1024,
    shell: false,
    timeout,
    windowsHide: true,
  });
  const processFailure = classifyGodotProcessFailure(result);
  if (processFailure !== null) fail("GODOT_COMMAND_FAILED", processFailure);
  return `${result.stdout ?? ""}${result.stderr ?? ""}`;
}

export function assertGodotOutputClean(output) {
  if (/\b(?:SCRIPT ERROR|ERROR:)\b/.test(output)) {
    fail("GODOT_OUTPUT_CONTAINS_ERROR");
  }
}

export function assertSingleReadinessMarker(output) {
  const count = output.split(GODOT_READINESS_MARKER).length - 1;
  if (count !== 1) {
    fail("GODOT_READINESS_MARKER_INVALID");
  }
}

export function assertGdUnitSuccess(output) {
  const text = output ?? "";
  const summaries = [...text.matchAll(
    /(\d+) test cases \| (\d+) errors \| (\d+) failures \| (\d+) flaky \| (\d+) skipped \| (\d+) orphans/gu,
  )];
  const summary = summaries.at(-1);
  const suites = /Executed test suites: \((\d+)\/(\d+)\)/u.exec(text);
  const cases = /Executed test cases\s+: \((\d+)\/(\d+)\)/u.exec(text);
  if (
    text.includes("No test cases found") ||
    !text.includes("Overall Summary:") ||
    !summary || Number(summary[1]) < 4 || summary.slice(2).some((value) => Number(value) !== 0) ||
    !suites || suites[1] !== suites[2] || Number(suites[1]) < 1 ||
    !cases || cases[1] !== cases[2] || Number(cases[1]) !== Number(summary[1])
  ) {
    fail("GDUNIT4_RESULT_INVALID");
  }
}

export function projectPath(moduleRoot) {
  return path.join(moduleRoot, "apps", "runtime-godot");
}
