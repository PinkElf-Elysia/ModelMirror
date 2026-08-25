import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { buildR17Mem0QualificationFromRaw } from "../scripts/lib/r17-agent-qualification-core.mjs";

const candidate = Object.freeze({ id: "mem0", lane: "memory-adapter", commit: "c".repeat(40), gitTreeSha1: "a".repeat(40) });
const sourceIdentityJson = canonicalizeJsonValue({ candidate: { id: "mem0", commit: candidate.commit } });
const audit = Buffer.from(canonicalizeJsonValue({ candidateId: "mem0", dependencyTreeStatus: "invalid" }));
const fixture = Buffer.from("const store = new Map();\nconst route = '/v3/memories/search/';\n");
const trace = `${Array.from({ length: 20 }, (_, index) => `MATRIX_OASIS_R17_MEM0_TRACE_JSON:{"trace":"stable"}\nR17_COMMAND_END:{"exitCode":0,"id":"semantic-${index + 1}","signal":""}`).join("\n")}\nR17_COMMAND_END:{"exitCode":1,"id":"dependency-tree","signal":""}\n`;

test("Mem0 SDK transport evidence cannot qualify a memory implementation owned by the fixture", () => {
  const result = buildR17Mem0QualificationFromRaw({ candidate, sourceIdentityJson, rawLogBytes: Buffer.from(trace), surfaceAuditBytes: audit, fixtureBytes: fixture });
  assert.equal(result.executionEvidence.deterministicRuns.count, 20);
  assert.equal(result.executionEvidence.deterministicRuns.uniqueTraceCount, 1);
  assert.equal(result.executionEvidence.observations.fixtureOwnsMemorySemantics, true);
  assert.equal(result.report.hardGates.authorityCompatibility, "not-proven");
  assert.equal(result.report.diagnosticCodes.includes("R17_MEM0_FIXTURE_TESTS_SDK_TRANSPORT_ONLY"), true);
  assert.equal(result.evaluation.conclusion, "deferred");
});

test("memory raw evidence produces deterministic bytes but changes when the fixture boundary changes", () => {
  const input = { candidate, sourceIdentityJson, rawLogBytes: Buffer.from(trace), surfaceAuditBytes: audit, fixtureBytes: fixture };
  const reports = Array.from({ length: 20 }, () => buildR17Mem0QualificationFromRaw(input));
  assert.equal(new Set(reports.map((result) => result.reportJson)).size, 1);
  const changed = buildR17Mem0QualificationFromRaw({ ...input, fixtureBytes: Buffer.from("export {};\n") });
  assert.notEqual(reports[0].reportJson, changed.reportJson);
  assert.equal(changed.executionEvidence.observations.fixtureOwnsMemorySemantics, false);
});
