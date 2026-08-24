import assert from "node:assert/strict";
import {
  createGodotSpatialSolutionVerifier,
  verifyPrototypeSpatialSolution,
} from "@matrix-oasis/prototype-spatial-verifier";
import { solvePrototypeSpatialLayout } from "@matrix-oasis/prototype-spatial-solver";
import { validatePrototypeEnvironmentFactsJson } from "@matrix-oasis/prototype-spatial-planning-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  buildSpatialVerificationFixture,
  roomGlbWithObstacle,
  sha256,
} from "../packages/prototype-spatial-verifier/tests/fixture.mjs";
import { resolveGodotBinary } from "../scripts/lib/godot-core.mjs";

function connectedDoubleSpaceFacts(baseFacts, spatialAssemblyJson, collider) {
  const facts = structuredClone(baseFacts);
  facts.source.collider.byteLength = collider.byteLength;
  facts.source.collider.sha256 = sha256(collider);
  facts.source.analysisTransform.sourceCanonicalSha256 = sha256(spatialAssemblyJson);
  facts.navigationMesh.verticesMm = [
    [-10_000, 0, -10_000], [-10_000, 0, 10_000], [-1_500, 0, 10_000], [-1_500, 0, -10_000],
    [1_500, 0, -10_000], [1_500, 0, 10_000], [10_000, 0, 10_000], [10_000, 0, -10_000],
    [-1_500, 0, 8_000], [1_500, 0, 8_000],
  ];
  facts.navigationMesh.polygons = [
    { vertexIndices: [0, 1, 2, 3], componentIndex: 0 },
    { vertexIndices: [4, 5, 6, 7], componentIndex: 0 },
    { vertexIndices: [8, 2, 5, 9], componentIndex: 0 },
  ];
  facts.navigationMesh.components = [{
    index: 0,
    polygonIndices: [0, 1, 2],
    bounds: { minimumMm: [-10_000, 0, -10_000], maximumMm: [10_000, 0, 10_000] },
  }];
  facts.floorAnchors = facts.floorAnchors.flatMap((anchor) => {
    const [x, , z] = anchor.positionMm;
    const polygonIndex = x <= -2_000 ? 0 : x >= 2_000 ? 1 : z >= 9_000 ? 2 : null;
    return polygonIndex === null ? [] : [{ ...anchor, polygonIndex }];
  });
  return facts;
}

async function main() {
  const fixture = await buildSpatialVerificationFixture();
  const collider = roomGlbWithObstacle([-0.5, 0, -12], [0.5, 3, 7]);
  const assembly = JSON.parse(fixture.spatialAssemblyJson);
  assembly.environment.collider.sha256 = sha256(collider);
  const spatialAssemblyJson = canonicalizeJsonValue(assembly);
  const environmentFactsJson = canonicalizeJsonValue(connectedDoubleSpaceFacts(
    JSON.parse(fixture.environmentFactsJson), spatialAssemblyJson, collider,
  ));
  const factsReport = validatePrototypeEnvironmentFactsJson(environmentFactsJson);
  assert.equal(factsReport.valid, true, JSON.stringify(factsReport));
  const solved = await solvePrototypeSpatialLayout({
    spatialIntentJson: fixture.spatialIntentJson,
    environmentFactsJson,
    assetBundleJson: fixture.assetBundleJson,
    runtimeGamePackJson: fixture.runtimeGamePackJson,
    runtimeReceiptJson: fixture.runtimeReceiptJson,
  });
  assert.equal(solved.ok, true, JSON.stringify(solved));
  assert.equal(new Set(solved.spatialSolution.navigation.zoneDomains.map((zone) => zone.zoneId)).size, 2);
  assert.equal(solved.spatialSolution.navigation.componentIndex, 0);
  const godot = resolveGodotBinary();
  const verified = await verifyPrototypeSpatialSolution({
    spatialIntentJson: fixture.spatialIntentJson,
    environmentFactsJson,
    spatialSolutionJson: solved.canonicalSpatialSolutionJson,
    assetBundleJson: fixture.assetBundleJson,
    runtimeGamePackJson: fixture.runtimeGamePackJson,
    runtimeReceiptJson: fixture.runtimeReceiptJson,
    spatialAssemblyJson,
    environmentColliderBytes: collider,
    environmentSplatBytes: fixture.environmentSplatBytes,
    assetFiles: fixture.assetFiles,
  }, createGodotSpatialSolutionVerifier({ godotBin: godot.command }));
  assert.equal(verified.ok, true, JSON.stringify(verified));
  const solution = JSON.parse(solved.canonicalSpatialSolutionJson);
  assert.equal(solution.nodeContexts.length > 0, true);
  assert.equal(solution.nodeContexts.every((context) =>
    context.playerSpawn.floorAnchorId !== context.actionTerminal.floorAnchorId &&
    context.actionTerminal.approachFloorAnchorId !== context.actionTerminal.floorAnchorId), true);
  assert.equal(new Set(solution.nodeContexts.map((context) => context.zoneId)).size, 2);
  process.stdout.write(`R16_GENERALIZATION_OK zones=2 polygons=3 anchors=${JSON.parse(environmentFactsJson).floorAnchors.length}\n`);
}

main().catch((error) => {
  process.stderr.write(`R16_GENERALIZATION_FAILED ${error?.message ?? "unknown"}\n`);
  process.exitCode = 1;
});
