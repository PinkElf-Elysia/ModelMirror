import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { R18LandscapeHarnessError, validateV2QualificationPlan } from "./plan.mjs";

const TMP_ROOT = path.resolve(path.win32.join("C:" + "\\", "tmp"));
const HASH = /^[0-9a-f]{64}$/u;
const CODE = /^[A-Z][A-Z0-9_]{2,95}$/u;
const EVIDENCE_FILES = Object.freeze(["execution-evidence.json", "qualification-plan.json", "qualification-report.json"]);

function fail(code) {
  throw new R18LandscapeHarnessError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function inside(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function capture(value, depth = 0) {
  if (depth > 32) fail("R18_QUALIFICATION_OPERATION_RESULT_INVALID");
  if (value === null || ["string", "boolean"].includes(typeof value)) return value;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) fail("R18_QUALIFICATION_OPERATION_RESULT_INVALID");
    return value;
  }
  if (!value || typeof value !== "object") fail("R18_QUALIFICATION_OPERATION_RESULT_INVALID");
  const prototype = Object.getPrototypeOf(value);
  if (![Object.prototype, Array.prototype, null].includes(prototype)) fail("R18_QUALIFICATION_OPERATION_RESULT_INVALID");
  if (Array.isArray(value)) return value.map((item) => capture(item, depth + 1));
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, capture(item, depth + 1)]));
}

function safeExistingDirectory(input, { allowRoot = false } = {}) {
  if (typeof input !== "string" || input.length === 0) fail("R18_QUALIFICATION_PATH_INVALID");
  const resolved = path.resolve(input);
  if (!inside(TMP_ROOT, resolved) || (!allowRoot && resolved === TMP_ROOT)) fail("R18_QUALIFICATION_PATH_OUTSIDE_TMP");
  let real;
  try {
    real = fs.realpathSync.native(resolved);
    const stat = fs.lstatSync(resolved);
    if (!stat.isDirectory() || stat.isSymbolicLink()) fail("R18_QUALIFICATION_DIRECTORY_INVALID");
  } catch (error) {
    if (error instanceof R18LandscapeHarnessError) throw error;
    fail("R18_QUALIFICATION_DIRECTORY_INVALID");
  }
  if (!inside(fs.realpathSync.native(TMP_ROOT), real)) fail("R18_QUALIFICATION_PATH_OUTSIDE_TMP");
  return real;
}

function safeNewOutput(input) {
  if (typeof input !== "string" || input.length === 0) fail("R18_QUALIFICATION_PATH_INVALID");
  const target = path.resolve(input);
  if (!inside(TMP_ROOT, target) || target === TMP_ROOT || fs.existsSync(target)) fail("R18_QUALIFICATION_OUTPUT_INVALID");
  safeExistingDirectory(path.dirname(target), { allowRoot: true });
  return target;
}

function readStable(filePath) {
  let handle;
  try {
    const before = fs.lstatSync(filePath, { bigint: true });
    if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n) fail("R18_QUALIFICATION_EVIDENCE_FILE_INVALID");
    handle = fs.openSync(filePath, "r");
    const opened = fs.fstatSync(handle, { bigint: true });
    const bytes = fs.readFileSync(handle);
    const after = fs.fstatSync(handle, { bigint: true });
    const current = fs.lstatSync(filePath, { bigint: true });
    for (const key of ["dev", "ino", "size", "mtimeNs", "ctimeNs"]) if (opened[key] !== before[key] || after[key] !== opened[key] || current[key] !== opened[key]) fail("R18_QUALIFICATION_EVIDENCE_FILE_INVALID");
    return bytes;
  } catch (error) {
    if (error instanceof R18LandscapeHarnessError) throw error;
    fail("R18_QUALIFICATION_EVIDENCE_FILE_INVALID");
  } finally {
    if (handle !== undefined) fs.closeSync(handle);
  }
}

function validateFixtureResult(result, expected) {
  const value = capture(result);
  const keys = Object.keys(value).sort();
  const metricKeys = value.metrics && typeof value.metrics === "object" && !Array.isArray(value.metrics) ? Object.keys(value.metrics) : [];
  if (JSON.stringify(keys) !== JSON.stringify(["diagnosticCodes", "fixtureId", "laneId", "metrics", "status", "traceSha256"].sort()) || value.fixtureId !== expected.fixtureId || value.laneId !== expected.laneId || !["passed", "failed", "evidence-gap"].includes(value.status) || !HASH.test(value.traceSha256 || "") || !Array.isArray(value.diagnosticCodes) || value.diagnosticCodes.some((code) => !CODE.test(code)) || metricKeys.length > 16 || metricKeys.some((key) => !/^[a-z][A-Za-z0-9]{0,63}$/u.test(key)) || Object.values(value.metrics || {}).some((item) => !Number.isSafeInteger(item) || item < 0)) fail("R18_QUALIFICATION_FIXTURE_RESULT_INVALID");
  return value;
}

function validateSourceResult(result, plan) {
  const value = capture(result);
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(["clean", "licenseEvidenceSha256", "lifecycleScriptsExecuted", "sourceIdentitySha256", "unknownBinaryCount"].sort()) || value.sourceIdentitySha256 !== plan.source.identitySha256 || value.licenseEvidenceSha256 !== plan.license.evidenceSha256 || value.clean !== true || value.lifecycleScriptsExecuted !== false || !Number.isSafeInteger(value.unknownBinaryCount) || value.unknownBinaryCount !== 0) fail("R18_QUALIFICATION_SOURCE_RESULT_INVALID");
  return value;
}

function validateCleanup(result, plan) {
  const value = capture(result);
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(["containerUsed", "credentialsInherited", "networkObservation", "residualProcesses", "unexpectedWrites"].sort()) || !Number.isSafeInteger(value.residualProcesses) || !Number.isSafeInteger(value.unexpectedWrites) || value.residualProcesses < 0 || value.unexpectedWrites < 0 || typeof value.credentialsInherited !== "boolean" || typeof value.containerUsed !== "boolean" || !["none-observed", "loopback-only-observed", "not-proven"].includes(value.networkObservation)) fail("R18_QUALIFICATION_CLEANUP_RESULT_INVALID");
  if (value.residualProcesses !== 0) fail("R18_QUALIFICATION_RESIDUAL_PROCESS");
  if (value.unexpectedWrites !== 0) fail("R18_QUALIFICATION_UNEXPECTED_WRITE");
  if (value.credentialsInherited) fail("R18_QUALIFICATION_SECRET_INHERITANCE");
  if (value.containerUsed && plan.approval.containerExecutionApproved !== true) fail("R18_CONTAINER_APPROVAL_REQUIRED");
  return value;
}

function makeReport(planJson, plan, executionJson, execution) {
  const diagnostics = new Set(["R18_FILESYSTEM_ISOLATION_NOT_PROVEN"]);
  if (execution.cleanup.networkObservation === "not-proven") diagnostics.add("R18_NETWORK_ISOLATION_NOT_PROVEN");
  for (const fixture of execution.fixtures) for (const code of fixture.diagnosticCodes) diagnostics.add(code);
  const anyFailure = execution.fixtures.some((fixture) => fixture.status === "failed");
  const anyGap = execution.fixtures.some((fixture) => fixture.status === "evidence-gap") || diagnostics.has("R18_NETWORK_ISOLATION_NOT_PROVEN");
  const status = anyFailure ? "failed" : anyGap ? "evidence-gap" : "executed";
  return {
    format: "matrix-oasis.v2-isolated-qualification-report",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidateId: plan.candidate.id,
    isolationClass: plan.candidate.isolationClass,
    laneIds: plan.candidate.laneIds,
    status,
    planSha256: sha256(Buffer.from(planJson, "utf8")),
    sourceIdentitySha256: plan.source.identitySha256,
    executionEvidenceSha256: sha256(Buffer.from(executionJson, "utf8")),
    fixtureCount: execution.fixtures.length,
    hardGates: {
      sourceIdentity: "pass",
      directLicense: "pass",
      credentials: "pass",
      filesystemIsolation: "not-proven",
      networkIsolation: execution.cleanup.networkObservation === "not-proven" ? "not-proven" : "observed-only",
      residualProcesses: "pass",
      runtimeFixture: anyFailure ? "fail" : anyGap ? "not-proven" : "pass",
    },
    diagnosticCodes: [...diagnostics].sort(),
  };
}

function validateReport(report, planJson, executionJson) {
  if (report?.format !== "matrix-oasis.v2-isolated-qualification-report" || report?.formatVersion !== "0.1.0" || report?.canonicalization !== "matrix-oasis.canonical-json/1" || !HASH.test(report.planSha256 || "") || !HASH.test(report.executionEvidenceSha256 || "") || report.planSha256 !== sha256(Buffer.from(planJson, "utf8")) || report.executionEvidenceSha256 !== sha256(Buffer.from(executionJson, "utf8")) || !Array.isArray(report.diagnosticCodes) || report.diagnosticCodes.some((code) => !CODE.test(code))) fail("R18_QUALIFICATION_REPORT_INVALID");
}

export function publishV2QualificationEvidence({ outputDir, planJson, executionEvidence, report }) {
  const target = safeNewOutput(outputDir);
  const parent = path.dirname(target);
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const reportJson = canonicalizeJsonValue(report);
  validateReport(report, planJson, executionJson);
  const payloads = new Map([
    ["qualification-plan.json", Buffer.from(planJson, "utf8")],
    ["execution-evidence.json", Buffer.from(executionJson, "utf8")],
    ["qualification-report.json", Buffer.from(reportJson, "utf8")],
  ]);
  const staging = path.join(parent, `.${path.basename(target)}.staging-${randomBytes(8).toString("hex")}`);
  try {
    fs.mkdirSync(staging);
    for (const [name, bytes] of payloads) fs.writeFileSync(path.join(staging, name), bytes, { flag: "wx" });
    fs.renameSync(staging, target);
  } catch (error) {
    if (fs.existsSync(staging)) fs.rmSync(staging, { recursive: true, force: true });
    if (error instanceof R18LandscapeHarnessError) throw error;
    fail("R18_QUALIFICATION_PUBLISH_FAILED");
  }
  return Object.freeze({ reportSha256: sha256(Buffer.from(reportJson, "utf8")), files: EVIDENCE_FILES });
}

export async function runV2Qualification({ planJson, sourceDir, outputDir, authorization }, operations) {
  const validation = validateV2QualificationPlan(planJson);
  if (!validation.valid) fail("R18_QUALIFICATION_PLAN_INVALID");
  const plan = validation.value;
  if (authorization?.candidateExecutionApproved !== true) fail("R18_CANDIDATE_EXECUTION_APPROVAL_REQUIRED");
  if (authorization?.dependencyDownloadApproved === true || authorization?.containerExecutionApproved === true) fail("R18_QUALIFICATION_AUTHORIZATION_SCOPE_INVALID");
  if (!operations || typeof operations.inspectSource !== "function" || typeof operations.executeFixture !== "function" || typeof operations.inspectCleanup !== "function") fail("R18_QUALIFICATION_OPERATIONS_INVALID");
  const source = safeExistingDirectory(sourceDir);
  safeNewOutput(outputDir);
  const context = Object.freeze({ sourceDir: source, candidateId: plan.candidate.id, isolationClass: plan.candidate.isolationClass, environment: Object.freeze({ credentials: "empty", network: plan.execution.network }), limits: Object.freeze({ timeoutMs: plan.execution.timeoutMs, outputMaxBytes: plan.execution.outputMaxBytes }) });
  const sourceInspection = validateSourceResult(await operations.inspectSource(Object.freeze({ context, expectedSource: plan.source, expectedLicense: plan.license })), plan);
  const fixtures = [];
  for (const fixture of plan.fixtures) fixtures.push(validateFixtureResult(await operations.executeFixture(Object.freeze({ context, fixture })), fixture));
  const cleanup = validateCleanup(await operations.inspectCleanup(Object.freeze({ context })), plan);
  const executionEvidence = {
    format: "matrix-oasis.v2-isolated-execution-evidence",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidateId: plan.candidate.id,
    isolationClass: plan.candidate.isolationClass,
    sourceInspection,
    fixtures,
    cleanup,
  };
  const executionJson = canonicalizeJsonValue(executionEvidence);
  if (Buffer.byteLength(executionJson) > plan.execution.outputMaxBytes) fail("R18_QUALIFICATION_OUTPUT_EXCEEDED");
  const report = makeReport(planJson, plan, executionJson, executionEvidence);
  const publication = publishV2QualificationEvidence({ outputDir, planJson, executionEvidence, report });
  return Object.freeze({ report: Object.freeze(report), publication });
}

export function verifyV2QualificationEvidenceDirectory(directory) {
  const root = safeExistingDirectory(directory);
  const names = fs.readdirSync(root).sort();
  if (JSON.stringify(names) !== JSON.stringify([...EVIDENCE_FILES].sort())) fail("R18_QUALIFICATION_EVIDENCE_SET_INVALID");
  const planJson = readStable(path.join(root, "qualification-plan.json")).toString("utf8");
  const executionJson = readStable(path.join(root, "execution-evidence.json")).toString("utf8");
  const reportJson = readStable(path.join(root, "qualification-report.json")).toString("utf8");
  const validation = validateV2QualificationPlan(planJson);
  if (!validation.valid) fail("R18_QUALIFICATION_PLAN_INVALID");
  let execution;
  let report;
  try {
    execution = JSON.parse(executionJson);
    report = JSON.parse(reportJson);
    if (canonicalizeJsonValue(execution) !== executionJson || canonicalizeJsonValue(report) !== reportJson) fail("R18_QUALIFICATION_EVIDENCE_NON_CANONICAL");
  } catch (error) {
    if (error instanceof R18LandscapeHarnessError) throw error;
    fail("R18_QUALIFICATION_EVIDENCE_INVALID");
  }
  validateReport(report, planJson, executionJson);
  if (execution.candidateId !== validation.value.candidate.id || report.candidateId !== validation.value.candidate.id || execution.fixtures.length !== report.fixtureCount) fail("R18_QUALIFICATION_EVIDENCE_IDENTITY_MISMATCH");
  return Object.freeze({ candidateId: report.candidateId, status: report.status, reportSha256: sha256(Buffer.from(reportJson, "utf8")) });
}
