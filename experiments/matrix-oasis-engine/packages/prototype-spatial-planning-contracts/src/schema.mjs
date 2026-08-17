export const PROTOTYPE_SPATIAL_INTENT_FORMAT =
  "matrix-oasis.prototype-spatial-intent";
export const PROTOTYPE_ENVIRONMENT_FACTS_FORMAT =
  "matrix-oasis.prototype-environment-facts";
export const PROTOTYPE_SPATIAL_PLANNING_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_SPATIAL_PLANNING_CANONICALIZATION =
  "matrix-oasis.canonical-json/1";

export const PROTOTYPE_SPATIAL_PLANNING_LIMITS = Object.freeze({
  documentDepth: 256,
  intentBytes: 2 * 1024 * 1024,
  factsBytes: 16 * 1024 * 1024,
  zones: 16,
  placements: 128,
  nodeContexts: 4096,
  constraintsPerPlacement: 32,
  navigationVertices: 200_000,
  navigationPolygons: 200_000,
  navigationComponents: 4096,
  floorAnchors: 65_536,
  wallAnchors: 65_536,
});

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

const SAFE = Number.MAX_SAFE_INTEGER;
const id = {
  type: "string",
  minLength: 1,
  maxLength: 96,
  pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
};
const contentVersion = {
  type: "string",
  minLength: 1,
  maxLength: 64,
  pattern: "\\S",
};
const hash = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
const integer = { type: "integer", minimum: -SAFE, maximum: SAFE };
const positionMm = {
  type: "array",
  minItems: 3,
  maxItems: 3,
  items: { type: "integer", minimum: -1_000_000, maximum: 1_000_000 },
};
const normalMicros = {
  type: "array",
  minItems: 3,
  maxItems: 3,
  items: { type: "integer", minimum: -1_000_000, maximum: 1_000_000 },
};

const identityDefs = {
  id,
  contentVersion,
  hash,
  scene: {
    type: "object",
    additionalProperties: false,
    required: ["id", "contentVersion"],
    properties: {
      id: { $ref: "#/$defs/id" },
      contentVersion: { $ref: "#/$defs/contentVersion" },
    },
  },
  blueprint: {
    type: "object",
    additionalProperties: false,
    required: ["format", "formatVersion", "canonicalSha256"],
    properties: {
      format: { const: "matrix-oasis.scene-blueprint" },
      formatVersion: { const: "0.1.0" },
      canonicalSha256: { $ref: "#/$defs/hash" },
    },
  },
  runtime: {
    type: "object",
    additionalProperties: false,
    required: ["format", "formatVersion", "id", "contentVersion", "sourceSha256", "artifactSha256"],
    properties: {
      format: { const: "matrix-oasis.runtime-game-pack" },
      formatVersion: { const: "0.1.0" },
      id: { $ref: "#/$defs/id" },
      contentVersion: { $ref: "#/$defs/contentVersion" },
      sourceSha256: { $ref: "#/$defs/hash" },
      artifactSha256: { $ref: "#/$defs/hash" },
    },
  },
  assetBundle: {
    type: "object",
    additionalProperties: false,
    required: ["format", "formatVersion", "canonicalSha256"],
    properties: {
      format: { const: "matrix-oasis.prototype-asset-bundle" },
      formatVersion: { const: "0.1.0" },
      canonicalSha256: { $ref: "#/$defs/hash" },
    },
  },
};

const spatialIntentSchema = {
  $id: "urn:matrix-oasis:prototype-spatial-intent:0.1.0",
  title: "Matrix Oasis Prototype Spatial Intent 0.1.0",
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "scene", "blueprint", "runtime", "assetBundle", "zones", "placements", "nodeContexts"],
  properties: {
    format: { const: PROTOTYPE_SPATIAL_INTENT_FORMAT },
    formatVersion: { const: PROTOTYPE_SPATIAL_PLANNING_FORMAT_VERSION },
    canonicalization: { const: PROTOTYPE_SPATIAL_PLANNING_CANONICALIZATION },
    scene: { $ref: "#/$defs/scene" },
    blueprint: { $ref: "#/$defs/blueprint" },
    runtime: { $ref: "#/$defs/runtime" },
    assetBundle: { $ref: "#/$defs/assetBundle" },
    zones: {
      type: "array",
      minItems: 1,
      maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.zones,
      items: { $ref: "#/$defs/zone" },
    },
    placements: {
      type: "array",
      maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.placements,
      items: { $ref: "#/$defs/placement" },
    },
    nodeContexts: {
      type: "array",
      minItems: 1,
      maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.nodeContexts,
      items: { $ref: "#/$defs/nodeContext" },
    },
  },
  $defs: {
    ...identityDefs,
    zone: {
      type: "object",
      additionalProperties: false,
      required: ["id", "adjacentZoneIds"],
      properties: {
        id: { $ref: "#/$defs/id" },
        adjacentZoneIds: {
          type: "array",
          uniqueItems: true,
          maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.zones - 1,
          items: { $ref: "#/$defs/id" },
        },
      },
    },
    distanceConstraint: {
      type: "object",
      additionalProperties: false,
      required: ["placementId", "distanceMm"],
      properties: {
        placementId: { $ref: "#/$defs/id" },
        distanceMm: { type: "integer", minimum: 1, maximum: 1_000_000 },
      },
    },
    facing: {
      oneOf: [
        {
          type: "object",
          additionalProperties: false,
          required: ["kind"],
          properties: { kind: { const: "none" } },
        },
        {
          type: "object",
          additionalProperties: false,
          required: ["kind"],
          properties: { kind: { const: "zone-center" } },
        },
        {
          type: "object",
          additionalProperties: false,
          required: ["kind", "placementId"],
          properties: {
            kind: { const: "placement" },
            placementId: { $ref: "#/$defs/id" },
          },
        },
      ],
    },
    placement: {
      type: "object",
      additionalProperties: false,
      required: ["id", "assetBriefId", "zoneId", "support", "anchor", "facing", "near", "separate", "clearanceClass"],
      properties: {
        id: { $ref: "#/$defs/id" },
        assetBriefId: { $ref: "#/$defs/id" },
        zoneId: { $ref: "#/$defs/id" },
        support: { enum: ["floor", "wall"] },
        anchor: { enum: ["free", "center", "edge"] },
        facing: { $ref: "#/$defs/facing" },
        near: {
          type: "array",
          maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.constraintsPerPlacement,
          items: { $ref: "#/$defs/distanceConstraint" },
        },
        separate: {
          type: "array",
          maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.constraintsPerPlacement,
          items: { $ref: "#/$defs/distanceConstraint" },
        },
        clearanceClass: { enum: ["compact", "human", "large"] },
      },
    },
    nodeContext: {
      type: "object",
      additionalProperties: false,
      required: ["nodeId", "zoneId", "visiblePlacementIds", "requiresPlayerSpawn", "requiresActionTerminal"],
      properties: {
        nodeId: { $ref: "#/$defs/id" },
        zoneId: { $ref: "#/$defs/id" },
        visiblePlacementIds: {
          type: "array",
          uniqueItems: true,
          maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.placements,
          items: { $ref: "#/$defs/id" },
        },
        requiresPlayerSpawn: { const: true },
        requiresActionTerminal: { const: true },
      },
    },
  },
};

const environmentFactsSchema = {
  $id: "urn:matrix-oasis:prototype-environment-facts:0.1.0",
  title: "Matrix Oasis Prototype Environment Facts 0.1.0",
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "source", "coordinateSystem", "analysisProfile", "environmentBounds", "navigationMesh", "floorAnchors", "wallAnchors"],
  properties: {
    format: { const: PROTOTYPE_ENVIRONMENT_FACTS_FORMAT },
    formatVersion: { const: PROTOTYPE_SPATIAL_PLANNING_FORMAT_VERSION },
    canonicalization: { const: PROTOTYPE_SPATIAL_PLANNING_CANONICALIZATION },
    source: { $ref: "#/$defs/source" },
    coordinateSystem: { $ref: "#/$defs/coordinateSystem" },
    analysisProfile: { $ref: "#/$defs/analysisProfile" },
    environmentBounds: { $ref: "#/$defs/bounds" },
    navigationMesh: { $ref: "#/$defs/navigationMesh" },
    floorAnchors: {
      type: "array",
      maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.floorAnchors,
      items: { $ref: "#/$defs/floorAnchor" },
    },
    wallAnchors: {
      type: "array",
      maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.wallAnchors,
      items: { $ref: "#/$defs/wallAnchor" },
    },
  },
  $defs: {
    id,
    contentVersion,
    hash,
    integer,
    positionMm,
    normalMicros,
    source: {
      type: "object",
      additionalProperties: false,
      required: ["scene", "blueprint", "runtime", "spatialEnvironmentBundle", "environmentBundleSha256", "collider", "calibration"],
      properties: {
        scene: { $ref: "#/$defs/scene" },
        blueprint: { $ref: "#/$defs/blueprint" },
        runtime: { $ref: "#/$defs/runtime" },
        spatialEnvironmentBundle: {
          type: "object",
          additionalProperties: false,
          required: ["format", "formatVersion", "canonicalSha256"],
          properties: {
            format: { const: "matrix-oasis.prototype-spatial-environment-bundle" },
            formatVersion: { const: "0.1.0" },
            canonicalSha256: { $ref: "#/$defs/hash" },
          },
        },
        environmentBundleSha256: { $ref: "#/$defs/hash" },
        collider: {
          type: "object",
          additionalProperties: false,
          required: ["format", "byteLength", "sha256"],
          properties: {
            format: { const: "glb" },
            byteLength: { type: "integer", minimum: 1, maximum: 33_554_432 },
            sha256: { $ref: "#/$defs/hash" },
          },
        },
        calibration: {
          type: "object",
          additionalProperties: false,
          required: ["coordinateTransform", "metricScaleMicros", "groundPlaneOffsetMm", "godotTranslationMm", "godotRotationMilliDegrees"],
          properties: {
            coordinateTransform: { const: "spz-raw-ply-to-godot-v1" },
            metricScaleMicros: { type: "integer", minimum: 1, maximum: 100_000_000 },
            groundPlaneOffsetMm: { type: "integer", minimum: -1_000_000, maximum: 1_000_000 },
            godotTranslationMm: { $ref: "#/$defs/positionMm" },
            godotRotationMilliDegrees: {
              type: "array",
              minItems: 3,
              maxItems: 3,
              items: { type: "integer", minimum: -360_000, maximum: 360_000 },
            },
          },
        },
      },
    },
    scene: identityDefs.scene,
    blueprint: identityDefs.blueprint,
    runtime: identityDefs.runtime,
    coordinateSystem: {
      type: "object",
      additionalProperties: false,
      required: ["handedness", "upAxis", "unit", "eulerOrder"],
      properties: {
        handedness: { const: "right" },
        upAxis: { const: "Y" },
        unit: { const: "millimeter" },
        eulerOrder: { const: "YXZ" },
      },
    },
    analysisProfile: {
      type: "object",
      additionalProperties: false,
      required: ["playerRadiusMm", "playerHeightMm", "floorSnapMm", "maxSlopeMilliDegrees"],
      properties: {
        playerRadiusMm: { const: 350 },
        playerHeightMm: { const: 1800 },
        floorSnapMm: { const: 200 },
        maxSlopeMilliDegrees: { const: 45_000 },
      },
    },
    bounds: {
      type: "object",
      additionalProperties: false,
      required: ["minimumMm", "maximumMm"],
      properties: {
        minimumMm: { $ref: "#/$defs/positionMm" },
        maximumMm: { $ref: "#/$defs/positionMm" },
      },
    },
    polygon: {
      type: "object",
      additionalProperties: false,
      required: ["vertexIndices", "componentIndex"],
      properties: {
        vertexIndices: {
          type: "array",
          minItems: 3,
          maxItems: 64,
          uniqueItems: true,
          items: { type: "integer", minimum: 0, maximum: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationVertices - 1 },
        },
        componentIndex: { type: "integer", minimum: 0, maximum: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationComponents - 1 },
      },
    },
    component: {
      type: "object",
      additionalProperties: false,
      required: ["index", "polygonIndices", "bounds"],
      properties: {
        index: { type: "integer", minimum: 0, maximum: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationComponents - 1 },
        polygonIndices: {
          type: "array",
          minItems: 1,
          uniqueItems: true,
          items: { type: "integer", minimum: 0, maximum: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationPolygons - 1 },
        },
        bounds: { $ref: "#/$defs/bounds" },
      },
    },
    navigationMesh: {
      type: "object",
      additionalProperties: false,
      required: ["verticesMm", "polygons", "components"],
      properties: {
        verticesMm: {
          type: "array",
          maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationVertices,
          items: { $ref: "#/$defs/positionMm" },
        },
        polygons: {
          type: "array",
          maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationPolygons,
          items: { $ref: "#/$defs/polygon" },
        },
        components: {
          type: "array",
          maxItems: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationComponents,
          items: { $ref: "#/$defs/component" },
        },
      },
    },
    floorAnchor: {
      type: "object",
      additionalProperties: false,
      required: ["id", "positionMm", "normalMicros", "clearanceRadiusMm", "clearanceHeightMm", "ceilingHeightMm", "componentIndex", "polygonIndex", "capsuleClearanceVerified"],
      properties: {
        id: { $ref: "#/$defs/id" },
        positionMm: { $ref: "#/$defs/positionMm" },
        normalMicros: { $ref: "#/$defs/normalMicros" },
        clearanceRadiusMm: { type: "integer", minimum: 350, maximum: 100_000 },
        clearanceHeightMm: { type: "integer", minimum: 1800, maximum: 100_000 },
        ceilingHeightMm: { type: "integer", minimum: 1800, maximum: 100_000 },
        componentIndex: { type: "integer", minimum: 0, maximum: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationComponents - 1 },
        polygonIndex: { type: "integer", minimum: 0, maximum: PROTOTYPE_SPATIAL_PLANNING_LIMITS.navigationPolygons - 1 },
        capsuleClearanceVerified: { const: true },
      },
    },
    wallAnchor: {
      type: "object",
      additionalProperties: false,
      required: ["id", "positionMm", "normalMicros", "availableWidthMm", "availableHeightMm", "nearestFloorAnchorId"],
      properties: {
        id: { $ref: "#/$defs/id" },
        positionMm: { $ref: "#/$defs/positionMm" },
        normalMicros: { $ref: "#/$defs/normalMicros" },
        availableWidthMm: { type: "integer", minimum: 1, maximum: 1_000_000 },
        availableHeightMm: { type: "integer", minimum: 1, maximum: 1_000_000 },
        nearestFloorAnchorId: { $ref: "#/$defs/id" },
      },
    },
  },
};

export const PROTOTYPE_SPATIAL_INTENT_SCHEMA = deepFreeze(spatialIntentSchema);
export const PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA = deepFreeze(environmentFactsSchema);
