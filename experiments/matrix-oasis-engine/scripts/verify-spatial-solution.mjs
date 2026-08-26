import assert from "node:assert/strict";
import {
  createGodotSpatialSolutionVerifier,
  verifyPrototypeSpatialSolution,
} from "@matrix-oasis/prototype-spatial-verifier";
import {
  buildSpatialVerificationFixture,
  roomGlbWithObstacle,
  sha256,
} from "../packages/prototype-spatial-verifier/tests/fixture.mjs";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { resolveGodotBinary } from "./lib/godot-core.mjs";

let stage = "initialization";

async function main() {
  stage = "godot-probe";
  const godot = resolveGodotBinary();
  const verifier = createGodotSpatialSolutionVerifier({ godotBin: godot.command });
  stage = "fixture";
  const fixture = await buildSpatialVerificationFixture();
  const fixtureIdentity = JSON.stringify({
    documents: [fixture.spatialIntentJson, fixture.environmentFactsJson, fixture.spatialSolutionJson, fixture.assetBundleJson, fixture.runtimeGamePackJson, fixture.runtimeReceiptJson],
    collider: [...fixture.environmentColliderBytes], assets: [...fixture.assetFiles].map(([key, value]) => [key, [...value]]),
  });
  stage = "verification";
  const result = await verifyPrototypeSpatialSolution(fixture, verifier);
  if (!result.ok) {
    const diagnostic = result.diagnostics[0];
    console.error(`SPATIAL_SOLUTION_VERIFICATION_FAILED stage=${stage} code=${diagnostic?.code ?? "PROTOTYPE_SPATIAL_VERIFY_UNKNOWN"} path=${diagnostic?.path ?? "/"}`);
    process.exitCode = 1;
    return;
  }
  assert.equal(result.verification.allChecksPassed, true);
  assert.equal(result.verification.placementCount, 2);
  assert.equal(result.verification.nodeContextCount > 0, true);
  const expectedTerminalCount = result.spatialSolution.nodeContexts.reduce((sum, context) =>
    sum + context.actionTerminal.actionCount, 0);
  assert.equal(result.verification.checkedPathCount, result.verification.checkedTerminalCount);
  assert.equal(result.verification.checkedPathCount, expectedTerminalCount);
  assert.equal(result.verification.checkedTerminalCount > 0, true);
  assert.equal(result.verification.checkedVisualSafetyBoxCount, 0);
  stage = "determinism";
  const repeated = await verifyPrototypeSpatialSolution(fixture, verifier);
  if (!repeated.ok) {
    const diagnostic = repeated.diagnostics[0];
    console.error(`SPATIAL_SOLUTION_VERIFICATION_FAILED stage=${stage} code=${diagnostic?.code ?? "PROTOTYPE_SPATIAL_VERIFY_UNKNOWN"} path=${diagnostic?.path ?? "/"}`);
    process.exitCode = 1;
    return;
  }
  assert.equal(repeated.canonicalVerificationReportJson, result.canonicalVerificationReportJson);
  stage = "multi-terminal-verification";
  const multiTerminalFixture = await buildSpatialVerificationFixture({ entryActionCount: 9 });
  const multiTerminal = await verifyPrototypeSpatialSolution(multiTerminalFixture, verifier);
  if (!multiTerminal.ok) {
    const diagnostic = multiTerminal.diagnostics[0];
    console.error(`SPATIAL_SOLUTION_VERIFICATION_FAILED stage=${stage} code=${diagnostic?.code ?? "PROTOTYPE_SPATIAL_VERIFY_UNKNOWN"} path=${diagnostic?.path ?? "/"}`);
    process.exitCode = 1;
    return;
  }
  const multiTerminalExpected = multiTerminal.spatialSolution.nodeContexts.reduce((sum, context) =>
    sum + context.actionTerminal.actionCount, 0);
  assert.equal(multiTerminal.spatialSolution.nodeContexts.some((context) => context.actionTerminal.actionCount === 9), true);
  assert.equal(multiTerminal.verification.checkedPathCount, multiTerminalExpected);
  assert.equal(multiTerminal.verification.checkedTerminalCount, multiTerminalExpected);
  stage = "terminal-support-integrity-rejection";
  const unsupportedFixture = { ...fixture };
  const unsupportedSolution = JSON.parse(fixture.spatialSolutionJson);
  unsupportedSolution.nodeContexts[0].actionTerminal.terminalSupports[0].baseHeightMm += 1;
  unsupportedFixture.spatialSolutionJson = canonicalizeJsonValue(unsupportedSolution);
  const unsupported = await verifyPrototypeSpatialSolution(unsupportedFixture, verifier);
  assert.equal(unsupported.ok, false);
  assert.equal(unsupported.diagnostics[0].code, "PROTOTYPE_SPATIAL_VERIFIER_TERMINAL_SUPPORT_MISMATCH");
  stage = "physical-rejection";
  const blockedFixture = { ...fixture };
  const blockedSolution = JSON.parse(fixture.spatialSolutionJson);
  blockedSolution.placements[0].positionMm = [12_000, 0, 0];
  blockedFixture.spatialSolutionJson = canonicalizeJsonValue(blockedSolution);
  const blocked = await verifyPrototypeSpatialSolution(blockedFixture, verifier);
  assert.equal(blocked.ok, false);
  assert.equal(blocked.diagnostics[0].code, "PROTOTYPE_SPATIAL_VERIFY_ASSET_GROUNDING_FAILED");
  stage = "real-environment-obstruction-rejection";
  const obstructedCollider = roomGlbWithObstacle([-0.7, 0, -2.45], [0.7, 2.2, -2.35]);
  const obstructedFacts = JSON.parse(fixture.environmentFactsJson);
  obstructedFacts.source.collider.byteLength = obstructedCollider.byteLength;
  obstructedFacts.source.collider.sha256 = sha256(obstructedCollider);
  const obstructedFactsJson = canonicalizeJsonValue(obstructedFacts);
  const obstructedSolution = JSON.parse(fixture.spatialSolutionJson);
  obstructedSolution.source.environmentFacts.canonicalSha256 = sha256(obstructedFactsJson);
  const obstructed = await verifyPrototypeSpatialSolution({
    ...fixture,
    environmentColliderBytes: obstructedCollider,
    environmentFactsJson: obstructedFactsJson,
    spatialSolutionJson: canonicalizeJsonValue(obstructedSolution),
  }, verifier);
  assert.equal(obstructed.ok, false);
  assert.equal(obstructed.diagnostics[0].code, "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_COLLISION");
  stage = "integrity-rejection";
  const changedFiles = new Map([...fixture.assetFiles].map(([key, value]) => [key, value.slice()]));
  changedFiles.values().next().value[0] ^= 0xff;
  const changed = await verifyPrototypeSpatialSolution({ ...fixture, assetFiles: changedFiles }, verifier);
  assert.equal(changed.ok, false);
  assert.equal(changed.diagnostics[0].code, "PROTOTYPE_SPATIAL_VERIFIER_ASSET_INTEGRITY_MISMATCH");
  assert.equal(JSON.stringify({
    documents: [fixture.spatialIntentJson, fixture.environmentFactsJson, fixture.spatialSolutionJson, fixture.assetBundleJson, fixture.runtimeGamePackJson, fixture.runtimeReceiptJson],
    collider: [...fixture.environmentColliderBytes], assets: [...fixture.assetFiles].map(([key, value]) => [key, [...value]]),
  }), fixtureIdentity);
  console.log(`SPATIAL_SOLUTION_VERIFICATION_OK placements=${result.verification.placementCount} nodes=${result.verification.nodeContextCount} paths=${result.verification.checkedPathCount} terminals=${result.verification.checkedTerminalCount}`);
}

main().catch((error) => {
  const code = typeof error?.code === "string" && /^PROTOTYPE_[A-Z0-9_]{2,127}$/u.test(error.code)
    ? error.code : "PROTOTYPE_SPATIAL_VERIFY_UNEXPECTED";
  const processStage = typeof error?.stage === "string" && /^(?:operation|probe|import|verification|result)$/u.test(error.stage)
    ? error.stage : "operation";
  const processFailure = typeof error?.processFailure === "string" &&
    /^(?:marker-missing|nonzero-exit|output-error|output-limit|signal|spawn-error|timeout|unknown)$/u.test(error.processFailure)
    ? error.processFailure : "unknown";
  console.error(`SPATIAL_SOLUTION_VERIFICATION_FAILED stage=${stage} code=${code} processStage=${processStage} process=${processFailure}`);
  process.exitCode = 1;
});
