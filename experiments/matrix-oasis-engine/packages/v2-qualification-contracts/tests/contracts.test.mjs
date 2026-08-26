import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { evaluateV2Candidate, rankV2Lane, validateV2CandidateLockJson, validateV2QualificationReportJson } from "../src/index.mjs";

function lock() {
  return {
    format: "matrix-oasis.v2-candidate-lock",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: {
      id: "beehave",
      lane: "godot-behavior-tree",
      repository: ["https:", "", "github.com", "bitbrain", "beehave"].join("/"),
      tag: "v2.9.3",
      commit: "773a5f6dd9b3433cdb8735ab35e9043d4cd60674",
      gitTreeSha1: "50bc821d8b0bd8581b0876307ec976f892f06327",
      treeListSha256: "c".repeat(64),
      sourceArchiveSha256: "a".repeat(64),
      license: "MIT",
      qualificationRoot: ".",
      upstreamLicense: { path: "LICENSE", byteLength: 1065, sha256: "b".repeat(64) },
    },
    executionPolicy: { containerAllowed: false, network: "none", lifecycleScriptsAllowed: false, timeoutMs: 120000, outputMaxBytes: 1048576, allowedProcessNames: ["godot.exe"] },
  };
}

function report({ gates = "pass", totalOffset = 0, status = "executed", id = "beehave" } = {}) {
  return {
    format: "matrix-oasis.v2-qualification-report",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: { id, lane: "godot-behavior-tree", commit: "7".repeat(40), gitTreeSha1: "5".repeat(40) },
    execution: { status, attemptCount: 1, commandCount: 3, networkObservation: gates, residualProcessObservation: gates },
    hardGates: { license: gates, reproducibleSource: gates, secretIsolation: gates, filesystemIsolation: gates, authorityCompatibility: gates, runtimeCompatibility: gates },
    scores: { architectureCompatibility: 22 + totalOffset, standaloneIntegration: 17, determinismTestability: 13, securityFailClosed: 13, maintenanceSourceRisk: 8, performanceRuntime: 8, functionality: 4 },
    evidence: { sourceIdentitySha256: "a".repeat(64), executionEvidenceSha256: "b".repeat(64), files: [{ name: "trace.json", byteLength: 64, sha256: "c".repeat(64) }] },
    switchConditions: [{ code: "SWITCH_IF_LOAD_FAILS", observable: "A locked 64-agent fixture misses its frame budget." }],
    diagnosticCodes: [],
  };
}

test("candidate lock and qualification report accept canonical closed documents", () => {
  const lockResult = validateV2CandidateLockJson(canonicalizeJsonValue(lock()));
  const reportResult = validateV2QualificationReportJson(canonicalizeJsonValue(report()));
  assert.equal(lockResult.valid, true);
  assert.equal(reportResult.valid, true);
  assert.equal(Object.isFrozen(lockResult), true);
  assert.equal(Object.isFrozen(reportResult.value), true);
});

test("unknown properties and non-canonical bytes fail with static diagnostics", () => {
  const unknown = lock();
  unknown.extra = "unapproved";
  const unknownResult = validateV2CandidateLockJson(canonicalizeJsonValue(unknown));
  assert.equal(unknownResult.valid, false);
  assert.ok(unknownResult.diagnostics.some((entry) => entry.code === "V2_CANDIDATE_LOCK_SCHEMA_UNKNOWN_PROPERTY"));
  const nonCanonical = validateV2QualificationReportJson(JSON.stringify(report(), null, 2));
  assert.equal(nonCanonical.valid, false);
  assert.equal(nonCanonical.diagnostics[0].code, "V2_QUALIFICATION_REPORT_JSON_NON_CANONICAL");
});

test("duplicate keys and unpaired surrogate text fail before semantics", () => {
  const canonical = canonicalizeJsonValue(lock());
  const duplicate = canonical.replace('"format":', '"format":"matrix-oasis.v2-candidate-lock","format":');
  assert.equal(validateV2CandidateLockJson(duplicate).diagnostics[0].code, "V2_CANDIDATE_LOCK_JSON_DUPLICATE_KEY");
  const bad = report();
  bad.switchConditions[0].observable = "\ud800";
  assert.equal(validateV2QualificationReportJson(canonicalizeJsonValue(bad)).diagnostics[0].code, "V2_QUALIFICATION_REPORT_TEXT_UNPAIRED_SURROGATE");
});

test("hard-gate failure rejects and unproven evidence defers regardless of score", () => {
  assert.equal(evaluateV2Candidate(report({ gates: "fail" })).conclusion, "rejected");
  assert.equal(evaluateV2Candidate(report({ gates: "not-proven", status: "deferred" })).conclusion, "deferred");
  assert.equal(evaluateV2Candidate(report({ status: "failed" })).conclusion, "rejected");
  assert.equal(evaluateV2Candidate(report({ gates: "not-proven", status: "failed" })).conclusion, "deferred");
});

test("score thresholds produce recommended, backup and rejected conclusions", () => {
  const recommended = report();
  const backup = report();
  backup.scores.architectureCompatibility = 8;
  const rejected = report();
  rejected.scores.architectureCompatibility = 0;
  rejected.scores.standaloneIntegration = 0;
  assert.deepEqual([evaluateV2Candidate(recommended).conclusion, evaluateV2Candidate(backup).conclusion, evaluateV2Candidate(rejected).conclusion], ["recommended", "backup", "rejected"]);
  assert.equal(evaluateV2Candidate(recommended).total, 85);
});

test("within five points the lower runtime surface wins the lane tie", () => {
  const larger = { ...evaluateV2Candidate(report({ id: "larger" })), runtimeSurface: 5 };
  const smallerReport = report({ id: "smaller" });
  smallerReport.scores.functionality = 3;
  const smaller = { ...evaluateV2Candidate(smallerReport), runtimeSurface: 1 };
  assert.equal(rankV2Lane([larger, smaller])[0].candidateId, "smaller");
});

test("twenty evaluations remain byte deterministic and do not mutate input", () => {
  const input = report();
  const before = canonicalizeJsonValue(input);
  const outputs = Array.from({ length: 20 }, () => canonicalizeJsonValue(evaluateV2Candidate(input)));
  assert.equal(new Set(outputs).size, 1);
  assert.equal(canonicalizeJsonValue(input), before);
});
