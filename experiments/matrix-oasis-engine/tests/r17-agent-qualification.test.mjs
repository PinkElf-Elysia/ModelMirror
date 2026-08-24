import assert from "node:assert/strict";
import test from "node:test";
import { buildR17AgentQualification, getR17AgentQualificationObservations } from "../scripts/lib/r17-agent-qualification-core.mjs";

test("Mem0 is a constrained backup rather than an authoritative memory store", () => {
  const result = buildR17AgentQualification(getR17AgentQualificationObservations()[0]);
  assert.equal(result.evaluation.total, 74);
  assert.equal(result.evaluation.conclusion, "backup");
  assert.equal(result.executionEvidence.deterministicRuns.count, 20);
  assert.equal(result.executionEvidence.deterministicRuns.uniqueTraceCount, 1);
  assert.equal(result.executionEvidence.fixture.restartRecovered, true);
  assert.equal(result.report.diagnosticCodes.includes("R17_MEM0_TELEMETRY_MUST_BE_DISABLED"), true);
});

test("Letta stays deferred instead of substituting its service README for runtime evidence", () => {
  const result = buildR17AgentQualification(getR17AgentQualificationObservations()[1]);
  assert.equal(result.report.execution.status, "deferred");
  assert.equal(result.evaluation.hardGatesPassed, false);
  assert.equal(result.evaluation.conclusion, "deferred");
});

test("memory reports remain canonical and byte-identical for twenty builds", () => {
  for (const observation of getR17AgentQualificationObservations()) {
    const reports = Array.from({ length: 20 }, () => buildR17AgentQualification(observation));
    assert.equal(new Set(reports.map((result) => result.reportJson)).size, 1);
    assert.equal(new Set(reports.map((result) => result.executionJson)).size, 1);
    assert.equal(Object.isFrozen(reports[0]), true);
  }
});
