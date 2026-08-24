import crypto from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { evaluateV2Candidate, validateV2QualificationReportJson } from "@matrix-oasis/v2-qualification-contracts";

const OBSERVATIONS = Object.freeze([
  Object.freeze({
    id: "mem0",
    lane: "memory-adapter",
    commit: "c427a453a89c5a3fee73cdb2e4c4df6a651e1692",
    gitTreeSha1: "a5c7228ce2a59c2391b4e0e22dfe7463bff8c4f9",
    sourceIdentitySha256: "828cdc2f341b04c9f19afcf3d7f2bc7d5a171aca6955557d264ed17b6188567b",
    execution: Object.freeze({ status: "executed", attemptCount: 2, commandCount: 23, networkObservation: "pass", residualProcessObservation: "pass" }),
    hardGates: Object.freeze({ license: "pass", reproducibleSource: "pass", secretIsolation: "pass", filesystemIsolation: "pass", authorityCompatibility: "pass", runtimeCompatibility: "pass" }),
    scores: Object.freeze({ architectureCompatibility: 20, standaloneIntegration: 10, determinismTestability: 14, securityFailClosed: 10, maintenanceSourceRisk: 8, performanceRuntime: 7, functionality: 5 }),
    files: Object.freeze([
      Object.freeze({ name: "mem0ai-3.1.6.tgz", byteLength: 831829, sha256: "f254557aa217a768daf7fed7acc2e7c36dd1ae133c1ad77b42ac9f99b95b93a8" }),
      Object.freeze({ name: "mem0-trace.log", byteLength: 153, sha256: "2bf1df18275371ce03b5e9dfc3b137b6d3d2aeaedb3ccd4c6b59bb0f464c0d10" }),
    ]),
    evidence: Object.freeze({ runs: 20, uniqueTraceCount: 1, fixture: Object.freeze({ add: 1, correct: 1, delete: 1, exports: 2, history: 2, isolatedSearches: 3, restartRecovered: true, requestCount: 11 }) }),
    diagnostics: Object.freeze(["R17_MEM0_TELEMETRY_MUST_BE_DISABLED", "R17_MEM0_REMOTE_SERVICE_NOT_AUTHORITY", "R17_MEM0_LOOPBACK_ADAPTER_ONLY"]),
    switchConditions: Object.freeze([
      Object.freeze({ code: "SWITCH_TO_MEM0_FOR_SEMANTIC_DERIVED_INDEX", observable: "A measured NPC recall workload exceeds the local deterministic index while ledger-derived rebuild and delete semantics remain intact." }),
    ]),
  }),
  Object.freeze({
    id: "letta",
    lane: "memory-adapter",
    commit: "1131535716e8a31c9a437f8695e25ac98f203a24",
    gitTreeSha1: "8d53781fa7c433a2071b578fcbae67b68063fa10",
    sourceIdentitySha256: "d1e215241b71ec6aac59bb718b0587870b574e4984d42dd16b4fd4876c446847",
    execution: Object.freeze({ status: "deferred", attemptCount: 0, commandCount: 0, networkObservation: "not-proven", residualProcessObservation: "not-proven" }),
    hardGates: Object.freeze({ license: "pass", reproducibleSource: "pass", secretIsolation: "not-proven", filesystemIsolation: "not-proven", authorityCompatibility: "not-proven", runtimeCompatibility: "not-proven" }),
    scores: Object.freeze({ architectureCompatibility: 12, standaloneIntegration: 3, determinismTestability: 4, securityFailClosed: 4, maintenanceSourceRisk: 4, performanceRuntime: 2, functionality: 5 }),
    files: Object.freeze([]),
    evidence: Object.freeze({ runs: 0, uniqueTraceCount: 0, fixture: Object.freeze({}) }),
    diagnostics: Object.freeze(["R17_LETTA_SERVICE_DATABASE_SURFACE_UNPROVEN", "R17_LETTA_PROVIDER_SURFACE_UNPROVEN", "R17_CONTAINER_APPROVAL_NOT_REQUESTED"]),
    switchConditions: Object.freeze([
      Object.freeze({ code: "REQUALIFY_LETTA_FOR_AGENT_SERVICE_BOUNDARY", observable: "A future round explicitly approves a separate agent service and proves database, provider and process isolation without replacing the World Event Ledger." }),
    ]),
  }),
]);

function hash(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(deepFreeze);
  return Object.freeze(value);
}

export function getR17AgentQualificationObservations() {
  return OBSERVATIONS;
}

export function buildR17AgentQualification(observation) {
  const executionEvidence = {
    format: "matrix-oasis.v2-execution-evidence",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidateId: observation.id,
    phase: observation.execution.status === "executed" ? "memory-loopback" : "dependency-surface-only",
    deterministicRuns: { count: observation.evidence.runs, uniqueTraceCount: observation.evidence.uniqueTraceCount },
    fixture: observation.evidence.fixture,
    diagnosticCodes: observation.diagnostics,
  };
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const report = {
    format: "matrix-oasis.v2-qualification-report",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: { id: observation.id, lane: observation.lane, commit: observation.commit, gitTreeSha1: observation.gitTreeSha1 },
    execution: observation.execution,
    hardGates: observation.hardGates,
    scores: observation.scores,
    evidence: { sourceIdentitySha256: observation.sourceIdentitySha256, executionEvidenceSha256: hash(executionJson), files: observation.files },
    switchConditions: observation.switchConditions,
    diagnosticCodes: observation.diagnostics,
  };
  const reportJson = canonicalizeJsonValue(report);
  const validation = validateV2QualificationReportJson(reportJson);
  if (!validation.valid) throw Object.assign(new Error("R17_AGENT_REPORT_INVALID"), { code: "R17_AGENT_REPORT_INVALID" });
  return deepFreeze({ executionEvidence, executionJson, report: validation.value, reportJson, evaluation: evaluateV2Candidate(validation.value) });
}
