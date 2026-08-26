import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { buildR17GodotQualificationFromRaw } from "../scripts/lib/r17-godot-qualification-core.mjs";

const candidates = Object.freeze({
  beehave: Object.freeze({ id: "beehave", lane: "godot-behavior-tree", commit: "7".repeat(40), gitTreeSha1: "5".repeat(40) }),
  limboai: Object.freeze({ id: "limboai", lane: "godot-behavior-tree", commit: "e".repeat(40), gitTreeSha1: "c".repeat(40) }),
  "dialogue-manager": Object.freeze({ id: "dialogue-manager", lane: "dialogue-presentation", commit: "f".repeat(40), gitTreeSha1: "b".repeat(40) }),
});

function identity(candidate) { return canonicalizeJsonValue({ candidate: { id: candidate.id, commit: candidate.commit } }); }
function audit(candidateId, additions = {}) { return Buffer.from(canonicalizeJsonValue({ candidateId, godotVersion: "4.6.3.stable", renderingMethod: "Forward+", licenseIds: ["MIT"], nativeBinaryCount: 0, nativeBinarySourceProvenance: "not-applicable", ...additions })); }
function log(prefix, count, exitCode = 0, extra = "") { return Buffer.from(`${Array.from({ length: count }, (_, index) => `${prefix}{"trace":"stable"}\nR17_COMMAND_END:{"exitCode":${exitCode},"id":"semantic-${index + 1}","signal":""}`).join("\n")}\n${extra}`); }

test("LimboAI cannot be recommended when the tested runtime bundle fails license and binary provenance gates", () => {
  const candidate = candidates.limboai;
  const input = { candidate, sourceIdentityJson: identity(candidate), rawLogBytes: log("MATRIX_OASIS_R17_LIMBO_TRACE_JSON:", 20), surfaceAuditBytes: audit(candidate.id, { licenseIds: ["MIT", "CC-BY-4.0"], nativeBinaryCount: 1, nativeBinarySourceProvenance: "not-proven" }) };
  const builds = Array.from({ length: 20 }, () => buildR17GodotQualificationFromRaw(input));
  assert.equal(new Set(builds.map((item) => item.reportJson)).size, 1);
  assert.equal(builds[0].report.hardGates.runtimeCompatibility, "pass");
  assert.equal(builds[0].report.hardGates.license, "fail");
  assert.equal(builds[0].evaluation.conclusion, "rejected");
});

test("Dialogue Manager remains deferred when only Compatibility rendering and a leaking fixture were observed", () => {
  const candidate = candidates["dialogue-manager"];
  const result = buildR17GodotQualificationFromRaw({ candidate, sourceIdentityJson: identity(candidate), rawLogBytes: log("MATRIX_OASIS_R17_DIALOGUE_TRACE_JSON:", 20, 0, "ObjectDB instances leaked at exit\n"), surfaceAuditBytes: audit(candidate.id, { renderingMethod: "Compatibility" }) });
  assert.equal(result.report.execution.status, "executed");
  assert.equal(result.report.hardGates.runtimeCompatibility, "not-proven");
  assert.equal(result.report.diagnosticCodes.includes("R17_DIALOGUE_FORWARD_PLUS_NOT_EXECUTED"), true);
  assert.equal(result.evaluation.conclusion, "deferred");
});

test("Beehave failure is retained as unproven rather than misattributed to upstream compatibility", () => {
  const candidate = candidates.beehave;
  const result = buildR17GodotQualificationFromRaw({ candidate, sourceIdentityJson: identity(candidate), rawLogBytes: log("MATRIX_OASIS_R17_BEEHAVE_TRACE_JSON:", 1, 101), surfaceAuditBytes: audit(candidate.id) });
  assert.equal(result.report.execution.status, "failed");
  assert.equal(result.report.hardGates.runtimeCompatibility, "not-proven");
  assert.equal(result.evaluation.conclusion, "deferred");
});

test("raw Godot trace changes alter the derived report instead of being ignored", () => {
  const candidate = candidates.limboai;
  const base = { candidate, sourceIdentityJson: identity(candidate), surfaceAuditBytes: audit(candidate.id, { licenseIds: ["CC-BY-4.0"] }) };
  const stable = buildR17GodotQualificationFromRaw({ ...base, rawLogBytes: log("MATRIX_OASIS_R17_LIMBO_TRACE_JSON:", 20) });
  const changed = buildR17GodotQualificationFromRaw({ ...base, rawLogBytes: log("MATRIX_OASIS_R17_LIMBO_TRACE_JSON:", 19) });
  assert.notEqual(stable.reportJson, changed.reportJson);
  assert.equal(changed.report.hardGates.runtimeCompatibility, "not-proven");
});
