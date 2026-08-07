import { spawnSync } from "node:child_process";
import {
  buildDoctorReport,
  doctorExitCode,
  extractVersion,
} from "./lib/doctor-core.mjs";
import { ACTIVE_ROUND } from "./lib/scope-policy.mjs";

const argumentsSet = new Set(process.argv.slice(2));
const jsonOutput = argumentsSet.has("--json");
const strictGodot = argumentsSet.has("--strict-godot");
const knownArguments = new Set(["--json", "--strict-godot"]);
const unknownArguments = [...argumentsSet].filter((argument) => !knownArguments.has(argument));

if (unknownArguments.length > 0) {
  console.error("DOCTOR_ARGUMENT_ERROR: unsupported option");
  process.exit(2);
}

function commandVersion(command, args) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    shell: false,
    timeout: 5_000,
    windowsHide: true,
  });

  if (result.error || result.status !== 0) {
    return null;
  }

  return extractVersion(`${result.stdout ?? ""} ${result.stderr ?? ""}`);
}

function detectGodot() {
  const candidates = [];
  if (process.env.GODOT_BIN) {
    candidates.push(process.env.GODOT_BIN);
  }
  candidates.push(process.platform === "win32" ? "godot.exe" : "godot", "godot4");

  for (const command of candidates) {
    const version = commandVersion(command, ["--version"]);
    if (version) {
      return version;
    }
  }

  return null;
}

function detectNpm() {
  const lifecycleVersion = /(?:^|\s)npm\/(\d+\.\d+(?:\.\d+){0,2})(?:\s|$)/.exec(
    process.env.npm_config_user_agent ?? "",
  )?.[1];
  if (lifecycleVersion) {
    return lifecycleVersion;
  }

  if (process.env.npm_execpath) {
    return commandVersion(process.execPath, [process.env.npm_execpath, "--version"]);
  }

  return process.platform === "win32"
    ? commandVersion("cmd.exe", ["/d", "/s", "/c", "npm.cmd --version"])
    : commandVersion("npm", ["--version"]);
}

const report = buildDoctorReport(
  {
    node: process.versions.node,
    npm: detectNpm(),
    git: commandVersion("git", ["--version"]),
    godot: detectGodot(),
  },
  { strictGodot },
);

if (jsonOutput) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`Matrix Oasis doctor: ${report.overallStatus}`);
  for (const check of report.checks) {
    const detected = check.detectedVersion ?? "not detected";
    const round = check.requiredForRound
      ? `${ACTIVE_ROUND} required`
      : "future optional";
    console.log(`[${check.status}] ${check.id}: ${detected} (${check.requirement}; ${round})`);
    if (check.status !== "ready") {
      console.log(`  Remediation: ${check.remediation}`);
    }
  }
}

process.exitCode = doctorExitCode(report);
