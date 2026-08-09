import { spawnSync } from "node:child_process";
import path from "node:path";

export const GODOT_REQUIRED_VERSION = "4.6.3";
export const GODOT_READINESS_MARKER =
  "MATRIX_OASIS_R4_GODOT_FOUNDATION_READY";

export class GodotHarnessError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotHarnessError";
    this.code = code;
  }
}

function fail(code) {
  throw new GodotHarnessError(code);
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
  if (result.error || result.status !== 0) {
    fail("GODOT_COMMAND_FAILED");
  }
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
  if (
    text.includes("No test cases found") ||
    !text.includes("Overall Summary:") ||
    !text.includes("4 test cases | 0 errors | 0 failures | 0 flaky | 0 skipped | 0 orphans") ||
    !text.includes("Executed test suites: (1/1)") ||
    !text.includes("Executed test cases : (4/4)")
  ) {
    fail("GDUNIT4_RESULT_INVALID");
  }
}

export function projectPath(moduleRoot) {
  return path.join(moduleRoot, "apps", "runtime-godot");
}
