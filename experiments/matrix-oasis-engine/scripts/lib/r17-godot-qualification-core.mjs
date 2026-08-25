import crypto from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { evaluateV2Candidate, validateV2QualificationReportJson } from "@matrix-oasis/v2-qualification-contracts";

const GODOT_IDS = new Set(["beehave", "limboai", "dialogue-manager"]);

function hash(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function freeze(value) { if (!value || typeof value !== "object" || Object.isFrozen(value)) return value; Object.values(value).forEach(freeze); return Object.freeze(value); }
function fail(code) { throw Object.assign(new Error(code), { code }); }
function artifact(name, bytes) { return Object.freeze({ name, byteLength: bytes.length, sha256: hash(bytes) }); }

function parseEnds(text) {
  const values = [];
  for (const line of text.split(/\r?\n/u)) {
    if (!line.startsWith("R17_COMMAND_END:")) continue;
    try { values.push(JSON.parse(line.slice("R17_COMMAND_END:".length))); } catch { fail("R17_RAW_COMMAND_END_INVALID"); }
  }
  return values;
}

function markerPayloads(text, prefix) {
  return text.split(/\r?\n/u).filter((line) => line.includes(prefix)).map((line) => line.slice(line.indexOf(prefix) + prefix.length));
}

function scoreFor(id) {
  if (id === "beehave") return { architectureCompatibility: 23, standaloneIntegration: 18, determinismTestability: 8, securityFailClosed: 4, maintenanceSourceRisk: 8, performanceRuntime: 4, functionality: 5 };
  if (id === "limboai") return { architectureCompatibility: 23, standaloneIntegration: 12, determinismTestability: 15, securityFailClosed: 3, maintenanceSourceRisk: 5, performanceRuntime: 9, functionality: 5 };
  return { architectureCompatibility: 18, standaloneIntegration: 12, determinismTestability: 8, securityFailClosed: 3, maintenanceSourceRisk: 6, performanceRuntime: 5, functionality: 5 };
}

function policyFor(candidateId, audit, facts) {
  const diagnostics = ["R17_FILESYSTEM_ISOLATION_NOT_PROVEN", "R17_SECRET_FILE_ISOLATION_NOT_PROVEN", "R17_NETWORK_ISOLATION_NOT_PROVEN"];
  let license = "not-proven";
  let runtimeCompatibility = "not-proven";
  const authorityCompatibility = "pass";
  let switchConditions;
  if (candidateId === "beehave") {
    diagnostics.push("R17_BEEHAVE_CONTROLLED_EXIT_NOT_PROVEN");
    switchConditions = [{ code: "REQUALIFY_BEEHAVE_AFTER_CLEAN_EXIT", observable: "The pinned suite and 20 semantic traces exit zero under Godot 4.6.3 with a complete allowed-license closure." }];
  } else if (candidateId === "limboai") {
    const forbiddenLicense = audit.licenseIds?.some((id) => !["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC0-1.0"].includes(id));
    license = forbiddenLicense ? "fail" : "not-proven";
    if (forbiddenLicense) diagnostics.push("R17_LIMBO_RUNTIME_LICENSE_CLOSURE_FAILED");
    if (audit.nativeBinaryCount > 0 && audit.nativeBinarySourceProvenance !== "proven") diagnostics.push("R17_LIMBO_NATIVE_BINARY_PROVENANCE_NOT_PROVEN");
    if (facts.semanticRuns === 20 && facts.semanticUnique === 1 && facts.allCommandsExitedZero) runtimeCompatibility = "pass";
    switchConditions = [{ code: "REQUALIFY_LIMBOAI_WITH_RUNTIME_ONLY_BUILD", observable: "A source-built runtime-only bundle has complete allowed-license closure and reproducible binary provenance on every target." }];
  } else {
    diagnostics.push("R17_DIALOGUE_RESTRICTIVE_ADAPTER_REQUIRED");
    if (audit.renderingMethod !== "Forward+") diagnostics.push("R17_DIALOGUE_FORWARD_PLUS_NOT_EXECUTED");
    if (facts.resourceLeakObserved) diagnostics.push("R17_GODOT_RESOURCE_LEAK_WARNING");
    if (facts.semanticRuns === 20 && facts.semanticUnique === 1 && facts.allCommandsExitedZero && audit.renderingMethod === "Forward+" && !facts.resourceLeakObserved) runtimeCompatibility = "pass";
    switchConditions = [{ code: "REQUALIFY_DIALOGUE_MANAGER_FOR_PRESENTATION", observable: "A restrictive Forward+ fixture completes 20 traces without leaks, expressions, mutations, dynamic loads or authority writes." }];
  }
  return { diagnostics: [...new Set(diagnostics)].sort(), hardGates: { license, reproducibleSource: "pass", secretIsolation: "not-proven", filesystemIsolation: "not-proven", authorityCompatibility, runtimeCompatibility }, switchConditions };
}

export function buildR17GodotQualificationFromRaw({ candidate, sourceIdentityJson, rawLogBytes, surfaceAuditBytes }) {
  if (!GODOT_IDS.has(candidate?.id) || !(rawLogBytes instanceof Buffer) || !(surfaceAuditBytes instanceof Buffer)) fail("R17_GODOT_RAW_INPUT_INVALID");
  let identity; let audit;
  try { identity = JSON.parse(sourceIdentityJson); audit = JSON.parse(surfaceAuditBytes.toString("utf8")); } catch { fail("R17_GODOT_RAW_INPUT_INVALID"); }
  if (identity.candidate?.id !== candidate.id || identity.candidate?.commit !== candidate.commit || audit.candidateId !== candidate.id) fail("R17_GODOT_RAW_IDENTITY_MISMATCH");
  const rawText = rawLogBytes.toString("utf8");
  const ends = parseEnds(rawText);
  const prefix = candidate.id === "limboai" ? "MATRIX_OASIS_R17_LIMBO_TRACE_JSON:" : candidate.id === "dialogue-manager" ? "MATRIX_OASIS_R17_DIALOGUE_TRACE_JSON:" : "MATRIX_OASIS_R17_BEEHAVE_TRACE_JSON:";
  const payloads = markerPayloads(rawText, prefix);
  const facts = { commandCount: ends.length, allCommandsExitedZero: ends.length > 0 && ends.every((item) => item.exitCode === 0), semanticRuns: payloads.length, semanticUnique: new Set(payloads).size, resourceLeakObserved: /ObjectDB instances leaked|resources still in use at exit/iu.test(rawText) };
  const policy = policyFor(candidate.id, audit, facts);
  const rawArtifact = artifact("runtime.log", rawLogBytes);
  const auditArtifact = artifact("surface-audit.json", surfaceAuditBytes);
  const executionEvidence = {
    format: "matrix-oasis.v2-execution-evidence", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", candidateId: candidate.id, phase: "recorded-godot-runtime",
    godot: { version: audit.godotVersion, renderer: audit.renderingMethod }, deterministicRuns: { count: facts.semanticRuns, uniqueTraceCount: facts.semanticUnique },
    observations: { allCommandsExitedZero: facts.allCommandsExitedZero, commandCount: facts.commandCount, resourceLeakObserved: facts.resourceLeakObserved },
    artifacts: [rawArtifact, auditArtifact].sort((left, right) => left.name.localeCompare(right.name)), diagnosticCodes: policy.diagnostics,
  };
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const sourceBytes = Buffer.from(sourceIdentityJson, "utf8");
  const executionBytes = Buffer.from(executionJson, "utf8");
  const report = {
    format: "matrix-oasis.v2-qualification-report", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    candidate: { id: candidate.id, lane: candidate.lane, commit: candidate.commit, gitTreeSha1: candidate.gitTreeSha1 },
    execution: { status: facts.allCommandsExitedZero ? "executed" : "failed", attemptCount: facts.semanticRuns, commandCount: facts.commandCount, networkObservation: "not-proven", residualProcessObservation: "not-proven" },
    hardGates: policy.hardGates, scores: scoreFor(candidate.id),
    evidence: { sourceIdentitySha256: hash(sourceBytes), executionEvidenceSha256: hash(executionBytes), files: [artifact("source-identity.json", sourceBytes), artifact("execution-evidence.json", executionBytes), rawArtifact, auditArtifact].sort((left, right) => left.name.localeCompare(right.name)) },
    switchConditions: policy.switchConditions, diagnosticCodes: policy.diagnostics,
  };
  const reportJson = canonicalizeJsonValue(report);
  const validation = validateV2QualificationReportJson(reportJson);
  if (!validation.valid) fail("R17_GODOT_REPORT_INVALID");
  return freeze({ executionEvidence, executionJson, report: validation.value, reportJson, evaluation: evaluateV2Candidate(validation.value), artifacts: [rawArtifact, auditArtifact] });
}
