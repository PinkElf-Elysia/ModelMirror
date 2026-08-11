import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  buildDoctorReport,
  doctorExitCode,
  extractVersion,
} from "../scripts/lib/doctor-core.mjs";

const readyRound = {
  node: "24.18.0",
  npm: "11.16.0",
  git: "2.50.1",
  godot: "4.6.3",
};

const moduleRoot = fileURLToPath(new URL("..", import.meta.url));

function runDoctor(args) {
  return spawnSync(process.execPath, ["scripts/doctor.mjs", ...args], {
    cwd: moduleRoot,
    encoding: "utf8",
    shell: false,
    timeout: 10_000,
    windowsHide: true,
  });
}

test("R5 blocks when the required Godot tool is missing", () => {
  const report = buildDoctorReport({ ...readyRound, godot: null });
  const godot = report.checks.find((check) => check.id === "godot");

  assert.equal(report.overallStatus, "blocked");
  assert.equal(doctorExitCode(report), 1);
  assert.equal(godot.requiredForRound, true);
  assert.equal(godot.status, "blocked");
  assert.equal(godot.detectedVersion, null);
});

test("required active-round tool mismatch blocks the report", () => {
  const report = buildDoctorReport({ ...readyRound, node: "23.9.0" });

  assert.equal(report.overallStatus, "blocked");
  assert.equal(doctorExitCode(report), 1);
  assert.equal(report.checks.find((check) => check.id === "node").status, "blocked");
});

test("R5 accepts only the exact Godot 4.6.3 patch line", () => {
  const missing = buildDoctorReport(
    { ...readyRound, godot: "4.6.2" },
    { strictGodot: true },
  );
  const ready = buildDoctorReport(
    { ...readyRound, godot: "4.6.3.stable.official" },
    { strictGodot: true },
  );

  assert.equal(missing.overallStatus, "blocked");
  assert.equal(doctorExitCode(missing), 1);
  assert.equal(ready.overallStatus, "ready");
  assert.equal(doctorExitCode(ready), 0);
});

test("version extraction emits version only", () => {
  assert.equal(extractVersion("git version 2.50.1.windows.1"), "2.50.1");
  assert.equal(extractVersion("Godot Engine v4.6.3.stable.official"), "4.6.3");
  assert.equal(extractVersion("not installed"), null);
});

test("doctor JSON contract is stable and excludes probe paths", () => {
  const separator = String.fromCharCode(92);
  const probePath = ["C:", "Users", "fixture-user", "bin"].join(separator);
  const gitVersion = extractVersion(`${probePath} git version 2.50.1`);
  const report = buildDoctorReport({ ...readyRound, git: gitVersion });

  assert.deepEqual(Object.keys(report).sort(), ["checks", "overallStatus"]);
  for (const check of report.checks) {
    assert.deepEqual(Object.keys(check).sort(), [
      "detectedVersion",
      "id",
      "remediation",
      "requiredForRound",
      "requirement",
      "status",
    ]);
  }
  assert.equal(JSON.stringify(report).includes("fixture-user"), false);
});

test("doctor CLI emits standalone machine-parseable JSON", () => {
  const result = runDoctor(["--json"]);

  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");

  const report = JSON.parse(result.stdout);
  assert.deepEqual(Object.keys(report).sort(), ["checks", "overallStatus"]);
  assert.equal(Array.isArray(report.checks), true);
  assert.equal(report.checks.length, 4);
  for (const check of report.checks) {
    assert.deepEqual(Object.keys(check).sort(), [
      "detectedVersion",
      "id",
      "remediation",
      "requiredForRound",
      "requirement",
      "status",
    ]);
  }
});

test("doctor CLI rejects unknown sensitive-looking options without echoing them", () => {
  const credentialPrefix = ["s", "k"].join("");
  const sensitiveValue = `${credentialPrefix}-${"A".repeat(32)}`;
  const optionName = ["--api", "key"].join("-");
  const result = runDoctor([`${optionName}=${sensitiveValue}`]);
  const combinedOutput = `${result.stdout ?? ""}${result.stderr ?? ""}`;

  assert.equal(result.error, undefined);
  assert.equal(result.status, 2);
  assert.equal(result.stdout, "");
  assert.equal(result.stderr.trim(), "DOCTOR_ARGUMENT_ERROR: unsupported option");
  assert.equal(combinedOutput.includes(sensitiveValue), false);
});
