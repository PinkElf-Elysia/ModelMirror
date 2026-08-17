import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import * as contracts from "../src/index.mjs";

const HASH_A = `sha256:${"a".repeat(64)}`;
const HASH_B = `sha256:${"b".repeat(64)}`;
const HASH_C = `sha256:${"c".repeat(64)}`;
const HASH_D = `sha256:${"d".repeat(64)}`;

function identity() {
  return {
    scene: { id: "neutral-space", contentVersion: "1" },
    blueprint: {
      format: "matrix-oasis.scene-blueprint",
      formatVersion: "0.1.0",
      canonicalSha256: HASH_A,
    },
    runtime: {
      format: "matrix-oasis.runtime-game-pack",
      formatVersion: "0.1.0",
      id: "neutral-space",
      contentVersion: "1",
      sourceSha256: HASH_B,
      artifactSha256: HASH_C,
    },
  };
}

function validIntent() {
  return {
    format: "matrix-oasis.prototype-spatial-intent",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    ...identity(),
    assetBundle: {
      format: "matrix-oasis.prototype-asset-bundle",
      formatVersion: "0.1.0",
      canonicalSha256: HASH_D,
    },
    zones: [
      { id: "room-a", adjacentZoneIds: ["room-b"] },
      { id: "room-b", adjacentZoneIds: ["room-a"] },
    ],
    placements: [
      {
        id: "object-a",
        assetBriefId: "brief-a",
        zoneId: "room-a",
        support: "floor",
        anchor: "center",
        facing: { kind: "zone-center" },
        near: [{ placementId: "object-b", distanceMm: 2400 }],
        separate: [],
        clearanceClass: "compact",
      },
      {
        id: "object-b",
        assetBriefId: "brief-b",
        zoneId: "room-b",
        support: "wall",
        anchor: "edge",
        facing: { kind: "placement", placementId: "object-a" },
        near: [],
        separate: [{ placementId: "object-a", distanceMm: 800 }],
        clearanceClass: "human",
      },
    ],
    nodeContexts: [
      {
        nodeId: "entry-node",
        zoneId: "room-a",
        visiblePlacementIds: ["object-a", "object-b"],
        requiresPlayerSpawn: true,
        requiresActionTerminal: true,
      },
    ],
  };
}

function validFacts() {
  return {
    format: "matrix-oasis.prototype-environment-facts",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    source: {
      ...identity(),
      spatialEnvironmentBundle: {
        format: "matrix-oasis.prototype-spatial-environment-bundle",
        formatVersion: "0.1.0",
        canonicalSha256: HASH_D,
      },
      environmentBundleSha256: HASH_A,
      collider: { format: "glb", byteLength: 4096, sha256: HASH_B },
      calibration: {
        coordinateTransform: "spz-raw-ply-to-godot-v1",
        metricScaleMicros: 1_000_000,
        groundPlaneOffsetMm: 0,
        godotTranslationMm: [0, 0, 0],
        godotRotationMilliDegrees: [0, 0, 0],
      },
      analysisTransform: {
        profile: "spatial-environment-calibration-v1",
        sourceCanonicalSha256: HASH_D,
        eulerOrder: "YXZ",
        root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] },
        collider: { localTranslationMm: [0, 0, 0], scaleMicros: 1_000_000 },
      },
    },
    coordinateSystem: {
      handedness: "right",
      upAxis: "Y",
      unit: "millimeter",
      eulerOrder: "YXZ",
    },
    analysisProfile: {
      playerRadiusMm: 350,
      playerHeightMm: 1800,
      floorSnapMm: 200,
      maxSlopeMilliDegrees: 45_000,
    },
    environmentBounds: {
      minimumMm: [-6000, -100, -6000],
      maximumMm: [6000, 4000, 6000],
    },
    navigationMesh: {
      verticesMm: [
        [-5000, 0, -5000],
        [-5000, 0, 5000],
        [5000, 0, 5000],
        [5000, 0, -5000],
      ],
      polygons: [{ vertexIndices: [0, 1, 2, 3], componentIndex: 0 }],
      components: [
        {
          index: 0,
          polygonIndices: [0],
          bounds: { minimumMm: [-5000, 0, -5000], maximumMm: [5000, 0, 5000] },
        },
      ],
    },
    floorAnchors: [
      {
        id: "floor-0000",
        positionMm: [0, 0, 0],
        normalMicros: [0, 1_000_000, 0],
        clearanceRadiusMm: 1200,
        clearanceHeightMm: 2400,
        ceilingHeightMm: 3000,
        componentIndex: 0,
        polygonIndex: 0,
        capsuleClearanceVerified: true,
      },
    ],
    wallAnchors: [
      {
        id: "wall-0000",
        positionMm: [0, 1200, -5000],
        normalMicros: [0, 0, 1_000_000],
        availableWidthMm: 2400,
        availableHeightMm: 2400,
        nearestFloorAnchorId: "floor-0000",
      },
    ],
  };
}

function validateIntent(value) {
  return contracts.validatePrototypeSpatialIntentJson(canonicalizeJsonValue(value));
}

function validateFacts(value) {
  return contracts.validatePrototypeEnvironmentFactsJson(canonicalizeJsonValue(value));
}

test("public surface and schemas are frozen", () => {
  assert.deepEqual(Object.keys(contracts).sort(), [
    "PROTOTYPE_ENVIRONMENT_FACTS_FORMAT",
    "PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA",
    "PROTOTYPE_SPATIAL_INTENT_FORMAT",
    "PROTOTYPE_SPATIAL_INTENT_SCHEMA",
    "PROTOTYPE_SPATIAL_PLANNING_CANONICALIZATION",
    "PROTOTYPE_SPATIAL_PLANNING_FORMAT_VERSION",
    "PROTOTYPE_SPATIAL_PLANNING_LIMITS",
    "PrototypeSpatialPlanningContractOperationalError",
    "validatePrototypeEnvironmentFactsJson",
    "validatePrototypeSpatialIntentJson",
  ]);
  assert.ok(Object.isFrozen(contracts.PROTOTYPE_SPATIAL_INTENT_SCHEMA));
  assert.ok(Object.isFrozen(contracts.PROTOTYPE_SPATIAL_INTENT_SCHEMA.$defs.placement));
  assert.ok(Object.isFrozen(contracts.PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA));
});

test("canonical intent and facts are valid and stable twenty times", () => {
  const intent = canonicalizeJsonValue(validIntent());
  const facts = canonicalizeJsonValue(validFacts());
  for (let run = 0; run < 20; run += 1) {
    assert.deepEqual(contracts.validatePrototypeSpatialIntentJson(intent), {
      reportVersion: 1,
      valid: true,
      diagnostics: [],
    });
    assert.deepEqual(contracts.validatePrototypeEnvironmentFactsJson(facts), {
      reportVersion: 1,
      valid: true,
      diagnostics: [],
    });
  }
});

test("parse, schema, surrogate, and canonical gates are strict and frozen", () => {
  const syntax = contracts.validatePrototypeSpatialIntentJson("{");
  assert.equal(syntax.diagnostics[0].code, "PROTOTYPE_SPATIAL_INTENT_JSON_SYNTAX");
  assert.ok(Object.isFrozen(syntax));
  assert.ok(Object.isFrozen(syntax.diagnostics));
  assert.equal(
    contracts.validatePrototypeSpatialIntentJson('{"format":1,"format":2}').diagnostics[0].code,
    "PROTOTYPE_SPATIAL_INTENT_JSON_DUPLICATE_KEY",
  );
  const unknown = validIntent();
  unknown.secretProviderTask = "not-returned";
  const unknownReport = validateIntent(unknown);
  assert.equal(unknownReport.diagnostics[0].code, "PROTOTYPE_SPATIAL_INTENT_SCHEMA_UNKNOWN_PROPERTY");
  assert.equal(JSON.stringify(unknownReport).includes("secretProviderTask"), false);
  const surrogate = validIntent();
  surrogate.scene.contentVersion = "\ud800";
  surrogate.runtime.contentVersion = "\ud800";
  const surrogateReport = validateIntent(surrogate);
  assert.equal(surrogateReport.diagnostics[0].phase, "semantic");
  assert.equal(surrogateReport.diagnostics[0].code, "PROTOTYPE_SPATIAL_INTENT_TEXT_UNPAIRED_SURROGATE");
  const text = canonicalizeJsonValue(validIntent());
  assert.equal(
    contracts.validatePrototypeSpatialIntentJson(`${text}\n`).diagnostics[0].code,
    "PROTOTYPE_SPATIAL_INTENT_JSON_NON_CANONICAL",
  );
  const deep = `${"[".repeat(257)}0${"]".repeat(257)}`;
  assert.equal(
    contracts.validatePrototypeEnvironmentFactsJson(deep).diagnostics[0].code,
    "PROTOTYPE_ENVIRONMENT_FACTS_JSON_DEPTH_EXCEEDED",
  );
});

test("intent rejects identity, topology, reference, and constraint errors", () => {
  const cases = [
    ["SPATIAL_INTENT_SCENE_ID_MISMATCH", (value) => { value.scene.id = "different-scene"; }],
    ["SPATIAL_INTENT_CONTENT_VERSION_MISMATCH", (value) => { value.scene.contentVersion = "2"; }],
    ["SPATIAL_INTENT_ZONE_ID_DUPLICATE", (value) => { value.zones[1].id = "room-a"; }],
    ["SPATIAL_INTENT_ZONE_ADJACENCY_ASYMMETRIC", (value) => { value.zones[1].adjacentZoneIds = []; }],
    ["SPATIAL_INTENT_ZONE_REFERENCE_NOT_FOUND", (value) => { value.placements[0].zoneId = "missing-zone"; }],
    ["SPATIAL_INTENT_PLACEMENT_ID_DUPLICATE", (value) => { value.placements[1].id = "object-a"; }],
    ["SPATIAL_INTENT_ASSET_BRIEF_DUPLICATE", (value) => { value.placements[1].assetBriefId = "brief-a"; }],
    ["SPATIAL_INTENT_PLACEMENT_REFERENCE_NOT_FOUND", (value) => { value.placements[1].facing.placementId = "missing-object"; }],
    ["SPATIAL_INTENT_PLACEMENT_SELF_REFERENCE", (value) => { value.placements[1].facing.placementId = "object-b"; }],
    ["SPATIAL_INTENT_CONSTRAINT_DUPLICATE", (value) => { value.placements[0].near.push({ placementId: "object-b", distanceMm: 2500 }); }],
    ["SPATIAL_INTENT_CONSTRAINT_CONFLICT", (value) => { value.placements[0].separate.push({ placementId: "object-b", distanceMm: 500 }); }],
    ["SPATIAL_INTENT_NODE_CONTEXT_DUPLICATE", (value) => { value.nodeContexts.push(structuredClone(value.nodeContexts[0])); }],
  ];
  for (const [code, mutate] of cases) {
    const value = validIntent();
    mutate(value);
    assert.ok(validateIntent(value).diagnostics.some((item) => item.code === code), code);
  }
});

test("intent forbids coordinates, paths, supplier fields, and false reachability flags", () => {
  for (const [container, key, value] of [
    ["root", "positionMm", [0, 0, 0]],
    ["placement", "path", "assets/object.glb"],
    ["placement", "provider", "external"],
    ["placement", "supplierTaskId", "sensitive"],
  ]) {
    const intent = validIntent();
    const target = container === "root" ? intent : intent.placements[0];
    target[key] = value;
    assert.ok(validateIntent(intent).diagnostics.some((item) => item.code === "PROTOTYPE_SPATIAL_INTENT_SCHEMA_UNKNOWN_PROPERTY"));
  }
  const flags = validIntent();
  flags.nodeContexts[0].requiresPlayerSpawn = false;
  assert.ok(validateIntent(flags).diagnostics.some((item) => item.code === "PROTOTYPE_SPATIAL_INTENT_SCHEMA_CONST"));
});

test("facts reject invalid navigation topology and component ownership", () => {
  const cases = [
    ["ENVIRONMENT_FACTS_BOUNDS_INVALID", (value) => { value.environmentBounds.maximumMm[0] = -6000; }],
    ["ENVIRONMENT_FACTS_NAVIGATION_EMPTY", (value) => { value.navigationMesh.verticesMm = []; value.navigationMesh.polygons = []; value.navigationMesh.components = []; }],
    ["ENVIRONMENT_FACTS_VERTEX_DUPLICATE", (value) => { value.navigationMesh.verticesMm[1] = [...value.navigationMesh.verticesMm[0]]; }],
    ["ENVIRONMENT_FACTS_VERTEX_OUTSIDE_BOUNDS", (value) => { value.navigationMesh.verticesMm[0][0] = -7000; }],
    ["ENVIRONMENT_FACTS_POLYGON_VERTEX_INDEX_INVALID", (value) => { value.navigationMesh.polygons[0].vertexIndices[3] = 4; }],
    ["ENVIRONMENT_FACTS_POLYGON_COMPONENT_INVALID", (value) => { value.navigationMesh.polygons[0].componentIndex = 1; }],
    ["ENVIRONMENT_FACTS_COMPONENT_INDEX_UNSTABLE", (value) => { value.navigationMesh.components[0].index = 1; }],
    ["ENVIRONMENT_FACTS_COMPONENT_POLYGON_INDEX_INVALID", (value) => { value.navigationMesh.components[0].polygonIndices[0] = 1; }],
    ["ENVIRONMENT_FACTS_POLYGON_COMPONENT_MISSING", (value) => {
      value.navigationMesh.polygons.push(structuredClone(value.navigationMesh.polygons[0]));
      value.navigationMesh.components[0].polygonIndices = [1];
    }],
    ["ENVIRONMENT_FACTS_COMPONENT_BOUNDS_MISMATCH", (value) => { value.navigationMesh.components[0].bounds.maximumMm[0] = 1000; }],
  ];
  for (const [code, mutate] of cases) {
    const value = validFacts();
    mutate(value);
    const diagnostics = validateFacts(value).diagnostics;
    assert.ok(diagnostics.some((item) => item.code === code), `${code}: ${JSON.stringify(diagnostics)}`);
  }
});

test("facts reject invalid floor and wall anchors", () => {
  const cases = [
    ["ENVIRONMENT_FACTS_ANCHOR_OUTSIDE_BOUNDS", (value) => { value.floorAnchors[0].positionMm[0] = 7000; }],
    ["ENVIRONMENT_FACTS_FLOOR_NORMAL_INVALID", (value) => { value.floorAnchors[0].normalMicros = [1_000_000, 0, 0]; }],
    ["ENVIRONMENT_FACTS_FLOOR_COMPONENT_MISMATCH", (value) => { value.floorAnchors[0].componentIndex = 1; }],
    ["ENVIRONMENT_FACTS_FLOOR_ANCHOR_OFF_NAVIGATION", (value) => { value.floorAnchors[0].positionMm = [5500, 0, 0]; }],
    ["ENVIRONMENT_FACTS_WALL_NORMAL_INVALID", (value) => { value.wallAnchors[0].normalMicros = [0, 0, 100]; }],
    ["ENVIRONMENT_FACTS_FLOOR_ANCHOR_REFERENCE_NOT_FOUND", (value) => { value.wallAnchors[0].nearestFloorAnchorId = "missing-floor"; }],
    ["ENVIRONMENT_FACTS_ANCHOR_ID_DUPLICATE", (value) => { value.wallAnchors[0].id = "floor-0000"; }],
  ];
  for (const [code, mutate] of cases) {
    const value = validFacts();
    mutate(value);
    const diagnostics = validateFacts(value).diagnostics;
    assert.ok(diagnostics.some((item) => item.code === code), `${code}: ${JSON.stringify(diagnostics)}`);
  }
});

test("facts identity and immutable profile are exact", () => {
  const mismatch = validFacts();
  mismatch.source.runtime.id = "other-scene";
  assert.ok(validateFacts(mismatch).diagnostics.some((item) => item.code === "ENVIRONMENT_FACTS_SCENE_ID_MISMATCH"));
  const profile = validFacts();
  profile.analysisProfile.playerRadiusMm = 351;
  assert.ok(validateFacts(profile).diagnostics.some((item) => item.code === "PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA_CONST"));
  const coordinate = validFacts();
  coordinate.coordinateSystem.eulerOrder = "XYZ";
  assert.ok(validateFacts(coordinate).diagnostics.some((item) => item.code === "PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA_CONST"));
  const hiddenTransform = validFacts();
  hiddenTransform.source.analysisTransform.profile = "implicit-fit";
  assert.ok(validateFacts(hiddenTransform).diagnostics.some((item) => item.code === "PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA_ENUM"));
  const unboundTransform = validFacts();
  unboundTransform.source.analysisTransform.sourceCanonicalSha256 = "sha256:bad";
  assert.ok(validateFacts(unboundTransform).diagnostics.some((item) => item.code === "PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA_STRING_CONSTRAINT"));
});

test("validation does not mutate inputs and operational errors remain static", () => {
  const intent = validIntent();
  const before = structuredClone(intent);
  validateIntent(intent);
  assert.deepEqual(intent, before);
  const error = new contracts.PrototypeSpatialPlanningContractOperationalError();
  assert.equal(error.code, "PROTOTYPE_SPATIAL_PLANNING_CONTRACT_INTERNAL_ERROR");
  assert.equal(error.message, error.code);
  assert.equal("cause" in error, false);
});
