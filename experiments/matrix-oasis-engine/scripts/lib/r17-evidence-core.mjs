import fs from "node:fs";
import path from "node:path";
import { verifyQualificationDirectory } from "@matrix-oasis/v2-qualification-harness";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { evaluateV2Candidate, validateV2QualificationReportJson } from "@matrix-oasis/v2-qualification-contracts";
import { buildR17GodotQualificationFromRaw } from "./r17-godot-qualification-core.mjs";
import { buildR17Mem0QualificationFromRaw } from "./r17-agent-qualification-core.mjs";

function fail(code) { const error = new Error(code); error.code = code; throw error; }

export function verifyR17EvidenceRoot(root) {
  const names = fs.readdirSync(root).sort();
  const direct = names.includes("qualification-report.json");
  const directories = direct ? [root] : names.map((name) => path.join(root, name));
  if (directories.length === 0) fail("R17_EVIDENCE_EMPTY");
  const reports = directories.map((directory) => {
    const verified = verifyQualificationDirectory(directory);
    const reportText = fs.readFileSync(path.join(directory, "qualification-report.json"), "utf8");
    const report = validateV2QualificationReportJson(reportText).value;
    const execution = JSON.parse(fs.readFileSync(path.join(directory, "execution-evidence.json"), "utf8"));
    if (execution.phase === "recorded-godot-runtime") {
      const rebuilt = buildR17GodotQualificationFromRaw({ candidate: report.candidate, sourceIdentityJson: fs.readFileSync(path.join(directory, "source-identity.json"), "utf8"), rawLogBytes: fs.readFileSync(path.join(directory, "runtime.log")), surfaceAuditBytes: fs.readFileSync(path.join(directory, "surface-audit.json")) });
      if (rebuilt.reportJson !== reportText) fail("R17_EVIDENCE_DERIVATION_MISMATCH");
    } else if (execution.phase === "recorded-sdk-transport") {
      const rebuilt = buildR17Mem0QualificationFromRaw({ candidate: report.candidate, sourceIdentityJson: fs.readFileSync(path.join(directory, "source-identity.json"), "utf8"), rawLogBytes: fs.readFileSync(path.join(directory, "runtime.log")), surfaceAuditBytes: fs.readFileSync(path.join(directory, "surface-audit.json")), fixtureBytes: fs.readFileSync(path.join(directory, "fixture.mjs.txt")) });
      if (rebuilt.reportJson !== reportText) fail("R17_EVIDENCE_DERIVATION_MISMATCH");
    } else if (execution.phase !== "source-only") fail("R17_EVIDENCE_PHASE_UNSUPPORTED");
    return verified;
  });
  const ids = reports.map((report) => report.candidateId);
  if (new Set(ids).size !== ids.length) fail("R17_EVIDENCE_DUPLICATE_CANDIDATE");
  return Object.freeze({ ok: true, candidates: Object.freeze(ids.sort()), reports: reports.length });
}

export function verifyR17QualificationSummary(moduleRoot) {
  const summaryPath = path.join(moduleRoot, "docs", "R17_QUALIFICATION_SUMMARY.json");
  const text = fs.readFileSync(summaryPath, "utf8");
  let summary;
  try { summary = JSON.parse(text); } catch { fail("R17_SUMMARY_INVALID"); }
  if (`${canonicalizeJsonValue(summary)}\n` !== text) fail("R17_SUMMARY_NON_CANONICAL");
  if (summary.schemaVersion !== 1 || summary.profile !== "matrix-oasis.v2-qualification/1" || summary.baseSha !== "66b57c3c83277bea960464decc2d4e46965a5ef1" || summary.status !== "r17-selection-qualified" || summary.providerRequests !== 0) fail("R17_SUMMARY_POLICY_INVALID");
  const ids = summary.candidates.map((candidate) => candidate.id);
  if (JSON.stringify(ids) !== JSON.stringify([...ids].sort()) || JSON.stringify(ids) !== JSON.stringify(["beehave", "dialogue-manager", "letta", "limboai", "mem0"])) fail("R17_SUMMARY_CANDIDATE_DRIFT");
  for (const candidate of summary.candidates) {
    if (!/^[0-9a-f]{64}$/u.test(candidate.reportSha256) || !/^[0-9a-f]{64}$/u.test(candidate.sourceIdentitySha256) || !/^[0-9a-f]{64}$/u.test(candidate.executionEvidenceSha256)) fail("R17_SUMMARY_CANDIDATE_DRIFT");
    const gateValues = Object.values(candidate.hardGates);
    if (!gateValues.includes("fail") && !gateValues.includes("not-proven")) fail("R17_SUMMARY_OVERCLAIMED_EXTERNAL_CANDIDATE");
    if (["recommended", "backup"].includes(candidate.conclusion)) fail("R17_SUMMARY_OVERCLAIMED_EXTERNAL_CANDIDATE");
    const synthetic = { candidate: { id: candidate.id, lane: candidate.lane }, execution: { status: candidate.executionStatus }, hardGates: candidate.hardGates, scores: candidate.scores, switchConditions: candidate.switchConditions };
    const evaluated = evaluateV2Candidate(synthetic);
    if (evaluated.conclusion !== candidate.conclusion || evaluated.total !== candidate.score) fail("R17_SUMMARY_CANDIDATE_DRIFT");
  }
  const decisions = new Map(summary.laneDecisions.map((decision) => [decision.lane, decision]));
  if (decisions.size !== 4 || decisions.get("godot-behavior-tree")?.recommended !== "native-runtime-state-machine-baseline" || decisions.get("dialogue-presentation")?.recommended !== "native-control" || decisions.get("memory-adapter")?.recommended !== "ledger-derived-index" || decisions.get("animation-fixture")?.recommended !== "no-r18-animation-dependency") fail("R17_SUMMARY_DECISION_INVALID");
  const status = JSON.parse(fs.readFileSync(path.join(moduleRoot, "docs", "V2_STATUS.json"), "utf8"));
  if (status.status !== summary.status || status.claimAllowed !== false || status.blockingRound !== "R24") fail("R17_SUMMARY_STATUS_MISMATCH");
  return Object.freeze({ ok: true, candidates: summary.candidates.length, lanes: decisions.size, status: summary.status });
}
