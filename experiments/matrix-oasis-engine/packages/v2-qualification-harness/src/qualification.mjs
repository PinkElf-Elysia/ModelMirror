import crypto from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { evaluateV2Candidate } from "@matrix-oasis/v2-qualification-contracts";
import { createCandidateLock, verifyCandidateCheckout } from "./source.mjs";
import { publishQualification } from "./publish.mjs";

function hash(text) { return crypto.createHash("sha256").update(text).digest("hex"); }

export function planCandidateQualification(candidate) {
  const lock = createCandidateLock(candidate);
  return Object.freeze({ planVersion: 1, candidateId: candidate.id, lane: candidate.lane, requiredSource: Object.freeze({ repository: candidate.repository, tag: candidate.tag, commit: candidate.commit, tree: candidate.gitTreeSha1 }), tools: Object.freeze(candidate.lane === "memory-adapter" ? ["git", candidate.id === "mem0" ? "node" : "python"] : ["git", "godot-4.6.3"]), behavior: Object.freeze({ network: lock.executionPolicy.network, container: "forbidden", lifecycleScripts: "ignore-and-audit", candidateProcesses: lock.executionPolicy.allowedProcessNames, timeoutMs: lock.executionPolicy.timeoutMs, outputMaxBytes: lock.executionPolicy.outputMaxBytes }) });
}

export function qualifySourceOnly({ candidate, sourceDir, outputDir }) {
  const candidateLock = createCandidateLock(candidate);
  const identity = verifyCandidateCheckout({ candidateLock, sourceDir });
  const executionEvidence = { format: "matrix-oasis.v2-execution-evidence", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", candidateId: candidate.id, phase: "source-only", executed: false, artifacts: [], diagnosticCodes: ["R17_LICENSE_CLOSURE_NOT_EXECUTED", "R17_RUNTIME_QUALIFICATION_NOT_EXECUTED"] };
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const report = {
    format: "matrix-oasis.v2-qualification-report",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: { id: candidate.id, lane: candidate.lane, commit: candidate.commit, gitTreeSha1: candidate.gitTreeSha1 },
    execution: { status: "deferred", attemptCount: 0, commandCount: 0, networkObservation: "not-proven", residualProcessObservation: "not-proven" },
    hardGates: { license: "not-proven", reproducibleSource: "pass", secretIsolation: "not-proven", filesystemIsolation: "not-proven", authorityCompatibility: "not-proven", runtimeCompatibility: "not-proven" },
    scores: { architectureCompatibility: 0, standaloneIntegration: 0, determinismTestability: 0, securityFailClosed: 0, maintenanceSourceRisk: 0, performanceRuntime: 0, functionality: 0 },
    evidence: { sourceIdentitySha256: identity.sha256, executionEvidenceSha256: hash(executionJson), files: [{ name: "source-identity.json", byteLength: Buffer.byteLength(identity.canonicalJson), sha256: identity.sha256 }, { name: "execution-evidence.json", byteLength: Buffer.byteLength(executionJson), sha256: hash(executionJson) }] },
    switchConditions: [{ code: "SWITCH_AFTER_RUNTIME_EVIDENCE", observable: "A bounded lane fixture completes all required runtime gates." }],
    diagnosticCodes: ["R17_LICENSE_CLOSURE_NOT_EXECUTED", "R17_RUNTIME_QUALIFICATION_NOT_EXECUTED"],
  };
  const evaluation = evaluateV2Candidate(report);
  const publication = publishQualification({ outputDir, sourceIdentityJson: identity.canonicalJson, executionEvidence, report });
  return Object.freeze({ candidateLock, evaluation, publication });
}
