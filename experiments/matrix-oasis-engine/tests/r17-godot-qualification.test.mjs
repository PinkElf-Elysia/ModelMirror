import assert from "node:assert/strict";
import test from "node:test";
import { buildR17GodotQualification, getR17GodotQualificationObservations } from "../scripts/lib/r17-godot-qualification-core.mjs";

test("Godot candidate observations remain pinned, canonical and deterministic", () => {
  const observations = getR17GodotQualificationObservations();
  assert.deepEqual(observations.map((item) => item.id), ["beehave", "limboai", "dialogue-manager"]);
  for (const observation of observations) {
    const builds = Array.from({ length: 20 }, () => buildR17GodotQualification(observation));
    assert.equal(new Set(builds.map((item) => item.reportJson)).size, 1, observation.id);
    assert.equal(new Set(builds.map((item) => item.executionJson)).size, 1, observation.id);
    assert.equal(Object.isFrozen(builds[0]), true);
  }
});

test("Beehave is rejected when its full green suite cannot exit cleanly", () => {
  const result = buildR17GodotQualification(getR17GodotQualificationObservations()[0]);
  assert.equal(result.report.execution.status, "failed");
  assert.equal(result.report.hardGates.runtimeCompatibility, "fail");
  assert.equal(result.evaluation.total, 87);
  assert.equal(result.evaluation.conclusion, "rejected");
});

test("LimboAI passes the deterministic behavior-tree lane despite its binary surface", () => {
  const result = buildR17GodotQualification(getR17GodotQualificationObservations()[1]);
  assert.equal(result.report.execution.status, "executed");
  assert.deepEqual(result.executionEvidence.metrics.agentLoads, [2, 4, 32, 64]);
  assert.equal(result.executionEvidence.deterministicRuns.count, 20);
  assert.equal(result.executionEvidence.deterministicRuns.uniqueTraceCount, 1);
  assert.equal(result.evaluation.total, 86);
  assert.equal(result.evaluation.conclusion, "recommended");
});

test("Dialogue Manager is only a narrow backup behind the restrictive presentation adapter", () => {
  const result = buildR17GodotQualification(getR17GodotQualificationObservations()[2]);
  assert.equal(result.report.diagnosticCodes.includes("R17_RESTRICTIVE_PRESENTATION_ADAPTER_REQUIRED"), true);
  assert.equal(result.report.diagnosticCodes.includes("R17_GODOT_RESOURCE_LEAK_WARNING"), true);
  assert.equal(result.evaluation.total, 71);
  assert.equal(result.evaluation.conclusion, "backup");
});
