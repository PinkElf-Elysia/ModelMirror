import crypto from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { evaluateV2Candidate, validateV2QualificationReportJson } from "@matrix-oasis/v2-qualification-contracts";

function hash(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function artifact(name, bytes) { return Object.freeze({ name, byteLength: bytes.length, sha256: hash(bytes) }); }
function freeze(value) { if (!value || typeof value !== "object" || Object.isFrozen(value)) return value; Object.values(value).forEach(freeze); return Object.freeze(value); }
function fail(code) { throw Object.assign(new Error(code), { code }); }

export function buildR17Mem0QualificationFromRaw({ candidate, sourceIdentityJson, rawLogBytes, surfaceAuditBytes, fixtureBytes }) {
  if (candidate?.id !== "mem0" || !(rawLogBytes instanceof Buffer) || !(surfaceAuditBytes instanceof Buffer) || !(fixtureBytes instanceof Buffer)) fail("R17_MEM0_RAW_INPUT_INVALID");
  let identity; let audit;
  try { identity = JSON.parse(sourceIdentityJson); audit = JSON.parse(surfaceAuditBytes.toString("utf8")); } catch { fail("R17_MEM0_RAW_INPUT_INVALID"); }
  if (identity.candidate?.id !== "mem0" || identity.candidate?.commit !== candidate.commit || audit.candidateId !== "mem0") fail("R17_MEM0_RAW_IDENTITY_MISMATCH");
  const rawText = rawLogBytes.toString("utf8");
  const prefix = "MATRIX_OASIS_R17_MEM0_TRACE_JSON:";
  const payloads = rawText.split(/\r?\n/u).filter((line) => line.includes(prefix)).map((line) => line.slice(line.indexOf(prefix) + prefix.length));
  const ends = rawText.split(/\r?\n/u).filter((line) => line.startsWith("R17_COMMAND_END:")).map((line) => JSON.parse(line.slice("R17_COMMAND_END:".length)));
  const fixtureText = fixtureBytes.toString("utf8");
  const fixtureOwnsMemorySemantics = /const store = new Map\(\)/u.test(fixtureText) && /memories\/search/u.test(fixtureText);
  const diagnostics = ["R17_FILESYSTEM_ISOLATION_NOT_PROVEN", "R17_SECRET_FILE_ISOLATION_NOT_PROVEN", "R17_NETWORK_ISOLATION_NOT_PROVEN", "R17_MEM0_TRANSITIVE_DEPENDENCIES_NOT_CLOSED"];
  if (fixtureOwnsMemorySemantics) diagnostics.push("R17_MEM0_FIXTURE_TESTS_SDK_TRANSPORT_ONLY");
  diagnostics.sort();
  const rawArtifact = artifact("runtime.log", rawLogBytes);
  const auditArtifact = artifact("surface-audit.json", surfaceAuditBytes);
  const fixtureArtifact = artifact("fixture.mjs.txt", fixtureBytes);
  const allCommandsExitedZero = ends.length > 0 && ends.every((item) => item.exitCode === 0);
  const executionEvidence = {
    format: "matrix-oasis.v2-execution-evidence", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", candidateId: "mem0", phase: "recorded-sdk-transport",
    deterministicRuns: { count: payloads.length, uniqueTraceCount: new Set(payloads).size }, observations: { allCommandsExitedZero, commandCount: ends.length, fixtureOwnsMemorySemantics, dependencyTreeStatus: audit.dependencyTreeStatus },
    artifacts: [rawArtifact, auditArtifact, fixtureArtifact].sort((left, right) => left.name.localeCompare(right.name)), diagnosticCodes: diagnostics,
  };
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const sourceBytes = Buffer.from(sourceIdentityJson, "utf8");
  const executionBytes = Buffer.from(executionJson, "utf8");
  const report = {
    format: "matrix-oasis.v2-qualification-report", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", candidate: { id: "mem0", lane: candidate.lane, commit: candidate.commit, gitTreeSha1: candidate.gitTreeSha1 },
    execution: { status: allCommandsExitedZero ? "executed" : "failed", attemptCount: payloads.length, commandCount: ends.length, networkObservation: "not-proven", residualProcessObservation: "not-proven" },
    hardGates: { license: "not-proven", reproducibleSource: "pass", secretIsolation: "not-proven", filesystemIsolation: "not-proven", authorityCompatibility: "not-proven", runtimeCompatibility: "not-proven" },
    scores: { architectureCompatibility: 16, standaloneIntegration: 8, determinismTestability: 10, securityFailClosed: 3, maintenanceSourceRisk: 5, performanceRuntime: 5, functionality: 2 },
    evidence: { sourceIdentitySha256: hash(sourceBytes), executionEvidenceSha256: hash(executionBytes), files: [artifact("source-identity.json", sourceBytes), artifact("execution-evidence.json", executionBytes), rawArtifact, auditArtifact, fixtureArtifact].sort((left, right) => left.name.localeCompare(right.name)) },
    switchConditions: [{ code: "REQUALIFY_MEM0_MEMORY_IMPLEMENTATION", observable: "A fixture exercises a real local memory implementation with closed dependencies, ledger rebuild/delete semantics and enforced process isolation instead of a test-owned store." }], diagnosticCodes: diagnostics,
  };
  const reportJson = canonicalizeJsonValue(report);
  const validation = validateV2QualificationReportJson(reportJson);
  if (!validation.valid) fail("R17_MEM0_REPORT_INVALID");
  return freeze({ executionEvidence, executionJson, report: validation.value, reportJson, evaluation: evaluateV2Candidate(validation.value), artifacts: [rawArtifact, auditArtifact, fixtureArtifact] });
}
