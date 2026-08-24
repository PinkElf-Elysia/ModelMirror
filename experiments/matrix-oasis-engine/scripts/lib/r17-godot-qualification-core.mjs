import crypto from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { evaluateV2Candidate, validateV2QualificationReportJson } from "@matrix-oasis/v2-qualification-contracts";

const SHA256 = /^[0-9a-f]{64}$/u;

const OBSERVATIONS = Object.freeze([
  Object.freeze({
    id: "beehave",
    lane: "godot-behavior-tree",
    commit: "773a5f6dd9b3433cdb8735ab35e9043d4cd60674",
    gitTreeSha1: "50bc821d8b0bd8581b0876307ec976f892f06327",
    sourceIdentitySha256: "f537a806445ff728cd28c26a81877910b6215561b773fad6ae6e129924dd2a02",
    attempts: 2,
    commands: 3,
    exitCode: 101,
    repeatedRuns: 1,
    traceVariants: 1,
    metrics: Object.freeze({ assertionsPassed: 168, assertionsTotal: 168, orphanNodes: 1, agentLoads: Object.freeze([]) }),
    files: Object.freeze([
      Object.freeze({ name: "beehave-full.log", byteLength: 821244, sha256: "1da1102442dc3b59c665921eef60d9234b8c6ceb6cc061e26e83bf09bd5c9013" }),
    ]),
    hardGates: Object.freeze({ license: "pass", reproducibleSource: "pass", secretIsolation: "pass", filesystemIsolation: "pass", authorityCompatibility: "pass", runtimeCompatibility: "fail" }),
    scores: Object.freeze({ architectureCompatibility: 23, standaloneIntegration: 18, determinismTestability: 12, securityFailClosed: 13, maintenanceSourceRisk: 8, performanceRuntime: 8, functionality: 5 }),
    diagnostics: Object.freeze(["R17_GODOT_CONTROLLED_EXIT_FAILED", "R17_GODOT_ORPHAN_NODE_OBSERVED"]),
    switchConditions: Object.freeze([
      Object.freeze({ code: "SWITCH_TO_BEEHAVE_AFTER_CLEAN_EXIT", observable: "The pinned Beehave suite exits zero under Godot 4.6.3 with no orphan-node or debugger transport failure." }),
    ]),
  }),
  Object.freeze({
    id: "limboai",
    lane: "godot-behavior-tree",
    commit: "e45e60e976dafab7f2c15cc341ae366e4cf3352b",
    gitTreeSha1: "c206e59fd2be90b228947d90e62e6821e7112f07",
    sourceIdentitySha256: "42374a8295ff78cbf7a73724c7dc12f173307a156194571ff4a4ae5401e1e20d",
    attempts: 2,
    commands: 22,
    exitCode: 0,
    repeatedRuns: 20,
    traceVariants: 1,
    metrics: Object.freeze({ assertionsPassed: 8, assertionsTotal: 8, orphanNodes: 0, agentLoads: Object.freeze([2, 4, 32, 64]) }),
    files: Object.freeze([
      Object.freeze({ name: "limbo-runtime.log", byteLength: 491, sha256: "681c6e461641a62b8d591c444e126889afc82008ad95677ba8e2b790036d9558" }),
      Object.freeze({ name: "limbo-gdextension-4.6.zip", byteLength: 32271237, sha256: "0910411f8dbc0da8920f6c8bbbb6397c347cd9f2f193942468a702dfb11d5f0e" }),
    ]),
    hardGates: Object.freeze({ license: "pass", reproducibleSource: "pass", secretIsolation: "pass", filesystemIsolation: "pass", authorityCompatibility: "pass", runtimeCompatibility: "pass" }),
    scores: Object.freeze({ architectureCompatibility: 23, standaloneIntegration: 14, determinismTestability: 15, securityFailClosed: 12, maintenanceSourceRisk: 8, performanceRuntime: 9, functionality: 5 }),
    diagnostics: Object.freeze(["R17_NATIVE_BINARY_DISTRIBUTION_REQUIRED"]),
    switchConditions: Object.freeze([
      Object.freeze({ code: "SWITCH_FROM_LIMBOAI_IF_BINARY_SURFACE_BLOCKS_RELEASE", observable: "A supported target lacks a pinned LimboAI binary or the binary distribution surface becomes unacceptable." }),
    ]),
  }),
  Object.freeze({
    id: "dialogue-manager",
    lane: "dialogue-presentation",
    commit: "ffc0011a1a3ea38fc6e65729e5f987d07dac0c88",
    gitTreeSha1: "b1b655d1737d2ae5fb1d5a9b7f3c0b67a83e7ecf",
    sourceIdentitySha256: "68fa97f767ddeff71885ea6b7cf3ded0ef53a61babd9392b9cad3bb1b03bad32",
    attempts: 3,
    commands: 4,
    exitCode: 0,
    repeatedRuns: 1,
    traceVariants: 1,
    metrics: Object.freeze({ assertionsPassed: 5, assertionsTotal: 5, orphanNodes: 0, agentLoads: Object.freeze([]) }),
    files: Object.freeze([
      Object.freeze({ name: "dialogue-runtime.log", byteLength: 500, sha256: "5e967ccce011e5242f6147ed81c7a2a96804df6ad7c7d1dea7134b02f7d03502" }),
    ]),
    hardGates: Object.freeze({ license: "pass", reproducibleSource: "pass", secretIsolation: "pass", filesystemIsolation: "pass", authorityCompatibility: "pass", runtimeCompatibility: "pass" }),
    scores: Object.freeze({ architectureCompatibility: 18, standaloneIntegration: 14, determinismTestability: 10, securityFailClosed: 10, maintenanceSourceRisk: 6, performanceRuntime: 8, functionality: 5 }),
    diagnostics: Object.freeze(["R17_RESTRICTIVE_PRESENTATION_ADAPTER_REQUIRED", "R17_GODOT_RESOURCE_LEAK_WARNING", "R17_UPSTREAM_PROJECT_REQUIRES_GODOT_4_7_CSHARP"]),
    switchConditions: Object.freeze([
      Object.freeze({ code: "SWITCH_TO_DIALOGUE_MANAGER_FOR_AUTHORED_BRANCHING_UI", observable: "R18 requires localized authored branching presentation that the existing native Control layer cannot provide, and the restrictive adapter exits without resource leaks." }),
    ]),
  }),
]);

function sha256(text) {
  return crypto.createHash("sha256").update(text).digest("hex");
}

function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

export function getR17GodotQualificationObservations() {
  return OBSERVATIONS;
}

export function buildR17GodotQualification(observation) {
  if (!observation || !SHA256.test(observation.sourceIdentitySha256)) throw Object.assign(new Error("R17_GODOT_OBSERVATION_INVALID"), { code: "R17_GODOT_OBSERVATION_INVALID" });
  const executionEvidence = {
    format: "matrix-oasis.v2-execution-evidence",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidateId: observation.id,
    phase: "godot-runtime",
    godot: { version: "4.6.3.stable.official.7d41c59c4", renderer: "Forward+", exitCode: observation.exitCode },
    deterministicRuns: { count: observation.repeatedRuns, uniqueTraceCount: observation.traceVariants },
    metrics: observation.metrics,
    diagnosticCodes: observation.diagnostics,
  };
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const report = {
    format: "matrix-oasis.v2-qualification-report",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: { id: observation.id, lane: observation.lane, commit: observation.commit, gitTreeSha1: observation.gitTreeSha1 },
    execution: { status: observation.exitCode === 0 ? "executed" : "failed", attemptCount: observation.attempts, commandCount: observation.commands, networkObservation: "pass", residualProcessObservation: "pass" },
    hardGates: observation.hardGates,
    scores: observation.scores,
    evidence: { sourceIdentitySha256: observation.sourceIdentitySha256, executionEvidenceSha256: sha256(executionJson), files: observation.files },
    switchConditions: observation.switchConditions,
    diagnosticCodes: observation.diagnostics,
  };
  const reportJson = canonicalizeJsonValue(report);
  const validation = validateV2QualificationReportJson(reportJson);
  if (!validation.valid) throw Object.assign(new Error("R17_GODOT_REPORT_INVALID"), { code: "R17_GODOT_REPORT_INVALID" });
  return freeze({ executionEvidence, executionJson, report: validation.value, reportJson, evaluation: evaluateV2Candidate(validation.value) });
}
