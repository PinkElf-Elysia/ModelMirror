import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createV2QualificationPlan,
  R18LandscapeHarnessError,
  runV2Qualification,
  validateV2QualificationPlan,
  verifyV2QualificationEvidenceDirectory,
} from "../src/index.mjs";

const TMP_ROOT = path.win32.join("C:" + "\\", "tmp");
const cleanup = [];

test.after(() => {
  for (const target of cleanup.reverse()) {
    const resolved = path.resolve(target);
    assert.equal(path.dirname(resolved), TMP_ROOT);
    assert.match(path.basename(resolved), /^matrix-oasis-r18-harness-/u);
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

function candidate(overrides = {}) {
  return {
    id: "fixture-candidate",
    candidateType: "open-source",
    laneIds: ["memory-relationships"],
    source: {
      kind: "git-repository",
      location: { host: "github.com", path: "example/fixture" },
      commit: "a".repeat(40),
      gitTreeSha1: "b".repeat(40),
      archiveSha256: null,
      identitySha256: "c".repeat(64),
    },
    license: { spdx: "MIT", reuseAllowed: false, qualificationAllowed: true, closureStatus: "direct-approved", evidenceSha256: "d".repeat(64) },
    surface: { runtimeClass: "service" },
    staticExclusion: { excluded: false, code: null },
    ...overrides,
  };
}

function tempSource() {
  const root = fs.mkdtempSync(path.join(TMP_ROOT, "matrix-oasis-r18-harness-source-"));
  cleanup.push(root);
  return root;
}

function tempOutput() {
  const target = path.join(TMP_ROOT, `matrix-oasis-r18-harness-output-${randomBytes(8).toString("hex")}`);
  cleanup.push(target);
  return target;
}

function operations(plan, { networkObservation = "not-proven", fixtureStatus = "passed" } = {}) {
  return {
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
      status: fixtureStatus,
      traceSha256: "e".repeat(64),
      metrics: { traces: 20 },
      diagnosticCodes: fixtureStatus === "evidence-gap" ? ["R18_FIXTURE_EVIDENCE_GAP"] : [],
    }),
    inspectCleanup: () => ({ residualProcesses: 0, unexpectedWrites: 0, credentialsInherited: false, containerUsed: false, networkObservation }),
  };
}

const authorization = Object.freeze({ candidateExecutionApproved: true, dependencyDownloadApproved: false, containerExecutionApproved: false });

test("service, embedded Godot and asset plans expose distinct fail-closed profiles", () => {
  const service = createV2QualificationPlan({ candidate: candidate(), laneIds: ["memory-relationships"] });
  const embedded = createV2QualificationPlan({ candidate: candidate({ laneIds: ["godot-behavior"], surface: { runtimeClass: "embedded-godot" } }), laneIds: ["godot-behavior"] });
  const asset = createV2QualificationPlan({ candidate: candidate({ candidateType: "public-asset", laneIds: ["character-animation"], surface: { runtimeClass: "asset" }, source: { kind: "source-archive", location: { host: "kenney.nl", path: "assets/fixture" }, commit: null, gitTreeSha1: null, archiveSha256: "f".repeat(64), identitySha256: "c".repeat(64) }, license: { spdx: "CC0-1.0", reuseAllowed: true, qualificationAllowed: true, closureStatus: "approved", evidenceSha256: "d".repeat(64) } }), laneIds: ["character-animation"] });
  assert.deepEqual([service.value.candidate.isolationClass, embedded.value.candidate.isolationClass, asset.value.candidate.isolationClass], ["service", "embedded-godot", "asset"]);
  assert.deepEqual([service.value.execution.network, embedded.value.execution.network, asset.value.execution.network], ["loopback-only", "none", "none"]);
  assert.equal(service.value.execution.container, "separate-candidate-approval-required");
  assert.equal(embedded.value.execution.container, "forbidden");
  assert.equal(validateV2QualificationPlan(service.canonicalJson).valid, true);
});

test("an archive-only source is published as an explicit identity evidence gap", async () => {
  const plan = createV2QualificationPlan({ candidate: candidate(), laneIds: ["memory-relationships"] });
  const injected = operations(plan, { networkObservation: "none-observed" });
  injected.inspectSource = () => ({
    sourceIdentitySha256: plan.value.source.identitySha256,
    licenseEvidenceSha256: plan.value.license.evidenceSha256,
    clean: true,
    identityStatus: "archive-only",
    lifecycleScriptsExecuted: false,
    unknownBinaryCount: 0,
  });
  const result = await runV2Qualification({ planJson: plan.canonicalJson, sourceDir: tempSource(), outputDir: tempOutput(), authorization }, injected);
  assert.equal(result.report.status, "evidence-gap");
  assert.equal(result.report.hardGates.sourceIdentity, "not-proven");
  assert.ok(result.report.diagnosticCodes.includes("R18_SOURCE_CHECKOUT_IDENTITY_NOT_PROVEN"));
});

test("commercial candidates and approval-bearing plan bytes are rejected", () => {
  assert.throws(
    () => createV2QualificationPlan({ candidate: candidate({ candidateType: "commercial-benchmark", surface: { runtimeClass: "commercial" } }), laneIds: ["memory-relationships"] }),
    (error) => error instanceof R18LandscapeHarnessError && error.code === "R18_COMMERCIAL_EXECUTION_FORBIDDEN",
  );
  const plan = createV2QualificationPlan({ candidate: candidate(), laneIds: ["memory-relationships"] });
  const changed = structuredClone(plan.value);
  changed.approval.candidateExecutionApproved = true;
  const validation = validateV2QualificationPlan(canonicalizeJsonValue(changed));
  assert.equal(validation.valid, false);
  assert.ok(validation.diagnostics.includes("R18_QUALIFICATION_PLAN_APPROVAL_INVALID"));
});

test("an authorized injected fixture publishes atomic evidence without overstating isolation", async () => {
  const plan = createV2QualificationPlan({ candidate: candidate(), laneIds: ["memory-relationships"] });
  const output = tempOutput();
  const result = await runV2Qualification({ planJson: plan.canonicalJson, sourceDir: tempSource(), outputDir: output, authorization }, operations(plan));
  assert.equal(result.report.status, "evidence-gap");
  assert.ok(result.report.diagnosticCodes.includes("R18_FILESYSTEM_ISOLATION_NOT_PROVEN"));
  assert.ok(result.report.diagnosticCodes.includes("R18_NETWORK_ISOLATION_NOT_PROVEN"));
  assert.ok(result.report.diagnosticCodes.includes("R18_PROCESS_TREE_RESIDUALS_NOT_PROVEN"));
  assert.equal(result.report.hardGates.residualProcesses, "not-proven");
  assert.deepEqual(fs.readdirSync(output).sort(), ["execution-evidence.json", "qualification-plan.json", "qualification-report.json"]);
  assert.deepEqual(verifyV2QualificationEvidenceDirectory(output), {
    candidateId: "fixture-candidate",
    status: "evidence-gap",
    reportSha256: result.publication.reportSha256,
  });
});

test("execution requires scoped approval and failures never publish a partial output", async () => {
  const plan = createV2QualificationPlan({ candidate: candidate(), laneIds: ["memory-relationships"] });
  const source = tempSource();
  const unapprovedOutput = tempOutput();
  await assert.rejects(
    runV2Qualification({ planJson: plan.canonicalJson, sourceDir: source, outputDir: unapprovedOutput, authorization: { ...authorization, candidateExecutionApproved: false } }, operations(plan)),
    (error) => error.code === "R18_CANDIDATE_EXECUTION_APPROVAL_REQUIRED",
  );
  assert.equal(fs.existsSync(unapprovedOutput), false);

  const failedOutput = tempOutput();
  const failed = operations(plan);
  failed.executeFixture = () => { throw new Error("fixture-owned failure"); };
  await assert.rejects(runV2Qualification({ planJson: plan.canonicalJson, sourceDir: source, outputDir: failedOutput, authorization }, failed));
  assert.equal(fs.existsSync(failedOutput), false);
});

test("secret inheritance, residual processes and unapproved containers fail closed", async () => {
  const plan = createV2QualificationPlan({ candidate: candidate(), laneIds: ["memory-relationships"] });
  for (const [field, value, code] of [
    ["credentialsInherited", true, "R18_QUALIFICATION_SECRET_INHERITANCE"],
    ["residualProcesses", 1, "R18_QUALIFICATION_RESIDUAL_PROCESS"],
    ["containerUsed", true, "R18_CONTAINER_APPROVAL_REQUIRED"],
  ]) {
    const output = tempOutput();
    const injected = operations(plan);
    injected.inspectCleanup = () => ({ residualProcesses: 0, unexpectedWrites: 0, credentialsInherited: false, containerUsed: false, networkObservation: "not-proven", [field]: value });
    await assert.rejects(runV2Qualification({ planJson: plan.canonicalJson, sourceDir: tempSource(), outputDir: output, authorization }, injected), (error) => error.code === code);
    assert.equal(fs.existsSync(output), false);
  }
});

test("evidence byte drift and linked reports are rejected", async () => {
  const plan = createV2QualificationPlan({ candidate: candidate(), laneIds: ["memory-relationships"] });
  const output = tempOutput();
  await runV2Qualification({ planJson: plan.canonicalJson, sourceDir: tempSource(), outputDir: output, authorization }, operations(plan));
  fs.appendFileSync(path.join(output, "execution-evidence.json"), "\n");
  assert.throws(() => verifyV2QualificationEvidenceDirectory(output), (error) => error.code === "R18_QUALIFICATION_EVIDENCE_NON_CANONICAL" || error.code === "R18_QUALIFICATION_REPORT_INVALID");
});

test("qualification plans are byte-identical for twenty runs and inputs remain unchanged", () => {
  const input = candidate();
  const before = structuredClone(input);
  const first = createV2QualificationPlan({ candidate: input, laneIds: ["memory-relationships"] });
  for (let index = 0; index < 20; index += 1) assert.equal(createV2QualificationPlan({ candidate: input, laneIds: ["memory-relationships"] }).canonicalJson, first.canonicalJson);
  assert.deepEqual(input, before);
});
