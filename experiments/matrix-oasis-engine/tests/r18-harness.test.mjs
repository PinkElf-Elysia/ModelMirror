import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { planAllR18Qualifications, planR18Qualification, qualifyR18Candidate } from "../scripts/lib/r18-harness-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("the desktop audit produces thirteen unique execution plans across all three isolation classes", () => {
  const plans = planAllR18Qualifications({ moduleRoot });
  assert.equal(plans.length, 13);
  assert.deepEqual([...new Set(plans.map((plan) => plan.value.candidate.isolationClass))].sort(), ["asset", "embedded-godot", "service"]);
  assert.equal(plans.some((plan) => plan.value.candidate.candidateType === "commercial-benchmark"), false);
  assert.equal(plans.some((plan) => plan.value.approval.candidateExecutionApproved), false);
  assert.equal(plans.some((plan) => plan.value.approval.containerExecutionApproved), false);
});

test("a shortlisted candidate gets one canonical multi-lane plan without executing source", () => {
  const plan = planR18Qualification({ moduleRoot, candidateId: "world-event-ledger-baseline" });
  assert.deepEqual(plan.value.candidate.laneIds, ["npc-orchestration", "memory-relationships", "dynamic-events", "evaluation-observability"]);
  assert.equal(plan.value.fixtures.length, 4);
  assert.equal(plan.value.execution.credentials, "empty");
  assert.match(plan.sha256, /^[0-9a-f]{64}$/u);
});

test("commercial references and non-shortlisted discovery hits cannot obtain execution plans", () => {
  for (const candidateId of ["inworld", "langgraph"]) {
    assert.throws(
      () => planR18Qualification({ moduleRoot, candidateId }),
      (error) => error.code === "R18_QUALIFICATION_CANDIDATE_NOT_SHORTLISTED",
    );
  }
});

test("the execution entrypoint delegates only to approved injected operations and publishes canonical evidence", async () => {
  const tempRoot = path.win32.join("C:" + "\\", "tmp");
  const sourceDir = fs.mkdtempSync(path.join(tempRoot, "matrix-oasis-r18-source-"));
  const outputDir = path.join(tempRoot, `matrix-oasis-r18-output-${process.pid}-${Date.now()}`);
  const plan = planR18Qualification({ moduleRoot, candidateId: "beehave" });
  const hash = (text) => createHash("sha256").update(text).digest("hex");
  const operations = {
    inspectSource: () => ({
      sourceIdentitySha256: plan.value.source.identitySha256,
      licenseEvidenceSha256: plan.value.license.evidenceSha256,
      clean: true,
      identityStatus: "proven",
      lifecycleScriptsExecuted: false,
      unknownBinaryCount: 0,
    }),
    executeFixture: ({ fixture }) => ({
      fixtureId: fixture.fixtureId,
      laneId: fixture.laneId,
      status: "passed",
      traceSha256: hash(fixture.fixtureId),
      metrics: { traceRuns: 20 },
      diagnosticCodes: [],
    }),
    inspectCleanup: () => ({
      residualProcesses: 0,
      unexpectedWrites: 0,
      credentialsInherited: false,
      containerUsed: false,
      networkObservation: "none-observed",
    }),
  };
  try {
    const result = await qualifyR18Candidate({ moduleRoot, candidateId: "beehave", sourceDir, outputDir }, { operations });
    assert.equal(result.report.status, "executed");
    assert.deepEqual(fs.readdirSync(outputDir).sort(), ["execution-evidence.json", "qualification-plan.json", "qualification-report.json"]);
  } finally {
    fs.rmSync(sourceDir, { recursive: true, force: true });
    fs.rmSync(outputDir, { recursive: true, force: true });
  }
});

test("the planning CLI returns canonical credential-free JSON and rejects extra arguments", () => {
  const executable = process.execPath;
  const script = path.join(moduleRoot, "scripts", "plan-r18-qualification.mjs");
  const result = spawnSync(executable, [script, "--candidate", "beehave"], { cwd: moduleRoot, encoding: "utf8", shell: false, windowsHide: true });
  assert.equal(result.status, 0);
  const text = result.stdout.trimEnd();
  assert.equal(JSON.stringify(JSON.parse(text)), text);
  assert.doesNotMatch(text, /(?:API[_-]?KEY|TOKEN|SECRET)/iu);
  assert.equal(path.win32.isAbsolute(JSON.parse(text).source.path), false);
  const rejected = spawnSync(executable, [script, "--candidate", "beehave", "--execute", "true"], { cwd: moduleRoot, encoding: "utf8", shell: false, windowsHide: true });
  assert.equal(rejected.status, 2);
  assert.equal(rejected.stderr.trim(), "R18_QUALIFICATION_PLAN_ARGUMENT_INVALID");
});

test("the Dialogue Manager adapter compiles fixed fixture text without ResourceLoader or lifecycle installation", () => {
  const adapter = fs.readFileSync(path.join(moduleRoot, "scripts", "lib", "r18-candidate-qualification-core.mjs"), "utf8");
  assert.match(adapter, /create_resource_from_text/u);
  assert.match(adapter, /R18_DIALOGUE_RUNTIME_LOG_NOT_CLEAN/u);
  assert.doesNotMatch(adapter, /load\("res:\/\/r18\.dialogue"\)/u);
  assert.doesNotMatch(adapter, /(?:npm|pnpm|yarn)\s+(?:install|ci)|postinstall/u);
});
