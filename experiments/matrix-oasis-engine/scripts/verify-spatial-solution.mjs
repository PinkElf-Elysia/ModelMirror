import assert from "node:assert/strict";
import {
  createGodotSpatialSolutionVerifier,
  verifyPrototypeSpatialSolution,
} from "@matrix-oasis/prototype-spatial-verifier";
import { buildSpatialVerificationFixture } from "../packages/prototype-spatial-verifier/tests/fixture.mjs";
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
  assert.equal(result.verification.checkedPathCount, result.verification.nodeContextCount);
  assert.equal(result.verification.checkedTerminalCount > 0, true);
  stage = "determinism";
  const repeated = await verifyPrototypeSpatialSolution(fixture, verifier);
  if (!repeated.ok) {
    const diagnostic = repeated.diagnostics[0];
    console.error(`SPATIAL_SOLUTION_VERIFICATION_FAILED stage=${stage} code=${diagnostic?.code ?? "PROTOTYPE_SPATIAL_VERIFY_UNKNOWN"} path=${diagnostic?.path ?? "/"}`);
    process.exitCode = 1;
    return;
  }
  assert.equal(repeated.canonicalVerificationReportJson, result.canonicalVerificationReportJson);
  stage = "physical-rejection";
  const blockedFixture = { ...fixture };
  const blockedSolution = JSON.parse(fixture.spatialSolutionJson);
  blockedSolution.placements[0].positionMm = [12_000, 0, 0];
  blockedFixture.spatialSolutionJson = canonicalizeJsonValue(blockedSolution);
  const blocked = await verifyPrototypeSpatialSolution(blockedFixture, verifier);
  assert.equal(blocked.ok, false);
  assert.equal(blocked.diagnostics[0].code, "PROTOTYPE_SPATIAL_VERIFY_ASSET_PENETRATION");
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

main().catch(() => {
  console.error(`SPATIAL_SOLUTION_VERIFICATION_FAILED stage=${stage}`);
  process.exitCode = 1;
});
