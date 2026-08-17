import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import * as api from "../src/index.mjs";

const hash = (character) => `sha256:${character.repeat(64)}`;
function fixture() {
  return {
    format: "matrix-oasis.prototype-spatial-solution",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    source: {
      spatialIntent: { format: "matrix-oasis.prototype-spatial-intent", formatVersion: "0.1.0", canonicalSha256: hash("a") },
      environmentFacts: { format: "matrix-oasis.prototype-environment-facts", formatVersion: "0.1.0", canonicalSha256: hash("b") },
      runtime: { format: "matrix-oasis.runtime-game-pack", formatVersion: "0.1.0", id: "fixture-pack", contentVersion: "0.1.0", sourceSha256: hash("c"), artifactSha256: hash("d") },
      runtimeReceiptSha256: hash("e"),
      assetBundle: { format: "matrix-oasis.prototype-asset-bundle", formatVersion: "0.1.0", canonicalSha256: hash("f") },
      analysisTransformSource: { profile: "spatial-assembly-collider-v1", format: "matrix-oasis.prototype-spatial-assembly", formatVersion: "0.1.0", canonicalSha256: hash("1") },
    },
    profile: {
      id: "matrix-oasis.spatial-solver/1",
      player: { radiusMm: 350, heightMm: 1800, floorSnapMm: 200 },
      clearanceMm: { compact: 250, human: 350, large: 600 },
      terminal: { widthMm: 1250, depthMm: 500, columns: 8, columnSpacingMm: 1700, rowSpacingMm: 2250, originZMm: -2400, interactionDistanceMm: 3000 },
      limits: { maxCandidatesPerItem: 256, maxSearchStates: 100000 },
      tolerances: { floorContactMm: 20, pathEndpointMm: 100 },
    },
    navigation: {
      componentIndex: 0,
      zoneSeeds: [{ zoneId: "zone-main", floorAnchorId: "floor-a" }],
      zoneDomains: [{ zoneId: "zone-main", componentIndex: 0, floorAnchorIds: ["floor-a", "floor-b"] }],
    },
    placements: [{ placementId: "placement-prop", anchorKind: "floor", anchorId: "floor-b", positionMm: [1000, 0, 1000], rotationMilliDegrees: [0, 0, 0], footprint: { widthMm: 1000, heightMm: 1000, depthMm: 1000 }, proof: { supportVerified: true, clearanceVerified: true, nonOverlapping: true } }],
    nodeContexts: [{ nodeId: "node-entry", zoneId: "zone-main", visiblePlacementIds: ["placement-prop"], playerSpawn: { floorAnchorId: "floor-a", positionMm: [0, 0, 0], yawMilliDegrees: 0 }, actionTerminal: { floorAnchorId: "floor-b", approachFloorAnchorId: "floor-a", positionMm: [1500, 0, 0], yawMilliDegrees: 0, actionCount: 2, footprint: { widthMm: 1250, depthMm: 500, layoutWidthMm: 2950, layoutDepthMm: 500, layoutCenterOffsetMm: [0, -2400] } }, approachPathFloorAnchorIds: ["floor-a", "floor-b"] }],
    metrics: { candidateCount: 8, expandedStates: 12 },
    proof: { allHardConstraintsSatisfied: true, singleNavigationComponent: true, allNodeApproachesReachable: true },
  };
}

test("public exports are exact and constants are deeply frozen", () => {
  assert.deepEqual(Object.keys(api).sort(), [
    "PROTOTYPE_SPATIAL_SOLUTION_CANONICALIZATION", "PROTOTYPE_SPATIAL_SOLUTION_FORMAT",
    "PROTOTYPE_SPATIAL_SOLUTION_FORMAT_VERSION", "PROTOTYPE_SPATIAL_SOLUTION_LIMITS",
    "PROTOTYPE_SPATIAL_SOLUTION_PROFILE", "PROTOTYPE_SPATIAL_SOLUTION_SCHEMA",
    "PrototypeSpatialSolutionContractOperationalError", "validatePrototypeSpatialSolutionJson",
  ].sort());
  assert.equal(Object.isFrozen(api.PROTOTYPE_SPATIAL_SOLUTION_SCHEMA), true);
  assert.equal(Object.isFrozen(api.PROTOTYPE_SPATIAL_SOLUTION_SCHEMA.$defs.source), true);
  assert.equal(Object.isFrozen(api.PROTOTYPE_SPATIAL_SOLUTION_PROFILE), true);
});

test("canonical golden solution validates and report is deeply frozen", () => {
  const report = api.validatePrototypeSpatialSolutionJson(canonicalizeJsonValue(fixture()));
  assert.deepEqual(report, { reportVersion: 1, valid: true, diagnostics: [] });
  assert.equal(Object.isFrozen(report), true);
  assert.equal(Object.isFrozen(report.diagnostics), true);
});

test("closed schema rejects missing, unknown, float, profile drift and bounds", () => {
  for (const mutate of [
    (value) => { delete value.profile; },
    (value) => { value.secret = "redacted"; },
    (value) => { value.metrics.expandedStates = 1.5; },
    (value) => { value.profile.player.radiusMm = 351; },
    (value) => { value.placements[0].positionMm[0] = 1000001; },
    (value) => { value.nodeContexts[0].actionTerminal.actionCount = 65; },
  ]) {
    const value = fixture(); mutate(value);
    const report = api.validatePrototypeSpatialSolutionJson(JSON.stringify(value));
    assert.equal(report.valid, false);
    assert.equal(report.diagnostics[0].phase, "schema");
  }
});

test("analysis transform source binds either the old assembly or direct environment bundle exactly", () => {
  const direct = fixture();
  direct.source.analysisTransformSource = { profile: "spatial-environment-calibration-v1", format: "matrix-oasis.prototype-spatial-environment-bundle", formatVersion: "0.1.0", canonicalSha256: hash("2") };
  assert.equal(api.validatePrototypeSpatialSolutionJson(canonicalizeJsonValue(direct)).valid, true);
  direct.source.analysisTransformSource.format = "matrix-oasis.prototype-spatial-assembly";
  assert.equal(api.validatePrototypeSpatialSolutionJson(canonicalizeJsonValue(direct)).valid, false);
});

test("semantic references, domains and approach evidence fail closed", () => {
  const cases = [
    ["PROTOTYPE_SPATIAL_SOLUTION_COMPONENT_MISMATCH", (value) => { value.navigation.zoneDomains[0].componentIndex = 1; }],
    ["PROTOTYPE_SPATIAL_SOLUTION_ZONE_SEED_OUTSIDE_DOMAIN", (value) => { value.navigation.zoneSeeds[0].floorAnchorId = "floor-c"; }],
    ["PROTOTYPE_SPATIAL_SOLUTION_PLACEMENT_REFERENCE_NOT_FOUND", (value) => { value.nodeContexts[0].visiblePlacementIds = ["placement-missing"]; }],
    ["PROTOTYPE_SPATIAL_SOLUTION_SPAWN_OUTSIDE_DOMAIN", (value) => { value.nodeContexts[0].playerSpawn.floorAnchorId = "floor-c"; }],
    ["PROTOTYPE_SPATIAL_SOLUTION_APPROACH_EVIDENCE_MISSING", (value) => { value.nodeContexts[0].actionTerminal.approachFloorAnchorId = "floor-b"; value.nodeContexts[0].approachPathFloorAnchorIds = ["floor-a"]; }],
  ];
  for (const [code, mutate] of cases) {
    const value = fixture(); mutate(value);
    const report = api.validatePrototypeSpatialSolutionJson(canonicalizeJsonValue(value));
    assert.equal(report.valid, false);
    assert.ok(report.diagnostics.some((item) => item.code === code));
  }
});

test("terminal aggregate footprint is derived exactly from action count", () => {
  for (const [actionCount, layoutWidthMm, layoutDepthMm, layoutCenterOffsetMm] of [
    [0, 1250, 500, [0, -2400]], [1, 1250, 500, [0, -2400]], [8, 13150, 500, [0, -2400]], [9, 13150, 2750, [0, -3525]], [64, 13150, 16250, [0, -10275]],
  ]) {
    const value = fixture();
    value.nodeContexts[0].actionTerminal.actionCount = actionCount;
    value.nodeContexts[0].actionTerminal.footprint.layoutWidthMm = layoutWidthMm;
    value.nodeContexts[0].actionTerminal.footprint.layoutDepthMm = layoutDepthMm;
    value.nodeContexts[0].actionTerminal.footprint.layoutCenterOffsetMm = layoutCenterOffsetMm;
    assert.equal(api.validatePrototypeSpatialSolutionJson(canonicalizeJsonValue(value)).valid, true);
    value.nodeContexts[0].actionTerminal.footprint.layoutWidthMm += layoutWidthMm === 13150 ? -1 : 1;
    assert.ok(api.validatePrototypeSpatialSolutionJson(canonicalizeJsonValue(value)).diagnostics.some((item) => item.code === "PROTOTYPE_SPATIAL_SOLUTION_TERMINAL_FOOTPRINT_MISMATCH"));
  }
});

test("strict JSON gates duplicate, depth, syntax and noncanonical bytes", () => {
  assert.equal(api.validatePrototypeSpatialSolutionJson("{}").valid, false);
  assert.equal(api.validatePrototypeSpatialSolutionJson('{"format":1,"format":2}').diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLUTION_JSON_DUPLICATE_KEY");
  assert.equal(api.validatePrototypeSpatialSolutionJson(`${canonicalizeJsonValue(fixture())}\n`).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLUTION_JSON_NON_CANONICAL");
  assert.equal(api.validatePrototypeSpatialSolutionJson("[").diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLUTION_JSON_SYNTAX");
  const deep = `${"[".repeat(257)}0${"]".repeat(257)}`;
  assert.equal(api.validatePrototypeSpatialSolutionJson(deep).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLUTION_JSON_DEPTH_EXCEEDED");
  const surrogate = fixture(); surrogate.source.runtime.contentVersion = String.fromCharCode(0xd800);
  assert.equal(api.validatePrototypeSpatialSolutionJson(canonicalizeJsonValue(surrogate)).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLUTION_TEXT_UNPAIRED_SURROGATE");
});

test("twenty validations are byte-stable and input text is unchanged", () => {
  const text = canonicalizeJsonValue(fixture());
  const reports = Array.from({ length: 20 }, () => JSON.stringify(api.validatePrototypeSpatialSolutionJson(text)));
  assert.equal(new Set(reports).size, 1);
  assert.equal(text, canonicalizeJsonValue(fixture()));
});
