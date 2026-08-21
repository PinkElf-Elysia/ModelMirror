export const PROTOTYPE_SPATIAL_SOLUTION_FORMAT = "matrix-oasis.prototype-spatial-solution";
export const PROTOTYPE_SPATIAL_SOLUTION_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_SPATIAL_SOLUTION_CANONICALIZATION = "matrix-oasis.canonical-json/1";
export const PROTOTYPE_SPATIAL_SOLUTION_PROFILE = Object.freeze({
  id: "matrix-oasis.spatial-solver/1",
  maxZones: 4, maxPlacements: 6, maxNodeContexts: 16, maxActionsPerNode: 64,
  maxCandidatesPerItem: 256, maxSearchStates: 100_000,
  playerRadiusMm: 350, playerHeightMm: 1800, playerEyeHeightMm: 1475, floorSnapMm: 200,
  compactClearanceMm: 250, humanClearanceMm: 350, largeClearanceMm: 600,
  terminalWidthMm: 1250, terminalDepthMm: 500, terminalColumns: 8,
  terminalColumnSpacingMm: 1700, terminalRowSpacingMm: 2250,
  terminalOriginZMm: -2400, terminalCenterHeightMm: 850,
  interactionDistanceMm: 3000, floorContactToleranceMm: 20,
  pathEndpointToleranceMm: 100,
});
export const PROTOTYPE_SPATIAL_SOLUTION_LIMITS = Object.freeze({
  documentDepth: 256, documentBytes: 16 * 1024 * 1024,
  coordinateMm: 1_000_000, rotationMilliDegrees: 360_000, footprintMm: 100_000,
});

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}
const id = { type: "string", minLength: 1, maxLength: 96, pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$" };
const hash = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
const contentVersion = { type: "string", minLength: 1, maxLength: 64, pattern: "\\S" };
const index = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER };
const coordinate = { type: "array", minItems: 3, maxItems: 3, items: { type: "integer", minimum: -1_000_000, maximum: 1_000_000 } };
const rotation = { type: "array", minItems: 3, maxItems: 3, items: { type: "integer", minimum: -360_000, maximum: 360_000 } };
const footprint = { type: "object", additionalProperties: false, required: ["widthMm", "heightMm", "depthMm"], properties: {
  widthMm: { type: "integer", minimum: 1, maximum: 100_000 }, heightMm: { type: "integer", minimum: 1, maximum: 100_000 }, depthMm: { type: "integer", minimum: 1, maximum: 100_000 },
} };
const hashIdentity = { type: "object", additionalProperties: false, required: ["format", "formatVersion", "canonicalSha256"], properties: {
  format: { type: "string", minLength: 1, maxLength: 96 }, formatVersion: { const: "0.1.0" }, canonicalSha256: { $ref: "#/$defs/hash" },
} };

const schema = {
  $id: "urn:matrix-oasis:prototype-spatial-solution:0.1.0",
  title: "Matrix Oasis Prototype Spatial Solution 0.1.0",
  type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "source", "profile", "navigation", "placements", "nodeContexts", "metrics", "proof"],
  properties: {
    format: { const: PROTOTYPE_SPATIAL_SOLUTION_FORMAT }, formatVersion: { const: "0.1.0" }, canonicalization: { const: "matrix-oasis.canonical-json/1" },
    source: { $ref: "#/$defs/source" }, profile: { $ref: "#/$defs/profile" }, navigation: { $ref: "#/$defs/navigation" },
    placements: { type: "array", maxItems: 6, items: { $ref: "#/$defs/placement" } },
    nodeContexts: { type: "array", minItems: 1, maxItems: 16, items: { $ref: "#/$defs/nodeContext" } },
    metrics: { $ref: "#/$defs/metrics" }, proof: { $ref: "#/$defs/proof" },
  },
  $defs: {
    id, hash, contentVersion, index, coordinate, rotation, footprint, hashIdentity,
    source: { type: "object", additionalProperties: false, required: ["spatialIntent", "environmentFacts", "runtime", "runtimeReceiptSha256", "assetBundle", "analysisTransformSource"], properties: {
      spatialIntent: { ...hashIdentity, properties: { ...hashIdentity.properties, format: { const: "matrix-oasis.prototype-spatial-intent" } } },
      environmentFacts: { ...hashIdentity, properties: { ...hashIdentity.properties, format: { const: "matrix-oasis.prototype-environment-facts" } } },
      runtime: { type: "object", additionalProperties: false, required: ["format", "formatVersion", "id", "contentVersion", "sourceSha256", "artifactSha256"], properties: {
        format: { const: "matrix-oasis.runtime-game-pack" }, formatVersion: { const: "0.1.0" }, id: { $ref: "#/$defs/id" }, contentVersion: { $ref: "#/$defs/contentVersion" }, sourceSha256: { $ref: "#/$defs/hash" }, artifactSha256: { $ref: "#/$defs/hash" },
      } },
      runtimeReceiptSha256: { $ref: "#/$defs/hash" },
      assetBundle: { ...hashIdentity, properties: { ...hashIdentity.properties, format: { const: "matrix-oasis.prototype-asset-bundle" } } },
      analysisTransformSource: { oneOf: [
        { type: "object", additionalProperties: false, required: ["profile", "format", "formatVersion", "canonicalSha256"], properties: { profile: { const: "spatial-assembly-collider-v1" }, format: { const: "matrix-oasis.prototype-spatial-assembly" }, formatVersion: { const: "0.1.0" }, canonicalSha256: { $ref: "#/$defs/hash" } } },
        { type: "object", additionalProperties: false, required: ["profile", "format", "formatVersion", "canonicalSha256"], properties: { profile: { const: "spatial-environment-calibration-v1" }, format: { const: "matrix-oasis.prototype-spatial-environment-bundle" }, formatVersion: { const: "0.1.0" }, canonicalSha256: { $ref: "#/$defs/hash" } } },
      ] },
    } },
    profile: { type: "object", additionalProperties: false, required: ["id", "player", "clearanceMm", "terminal", "limits", "tolerances"], properties: {
      id: { const: "matrix-oasis.spatial-solver/1" },
      player: { type: "object", additionalProperties: false, required: ["radiusMm", "heightMm", "eyeHeightMm", "floorSnapMm"], properties: { radiusMm: { const: 350 }, heightMm: { const: 1800 }, eyeHeightMm: { const: 1475 }, floorSnapMm: { const: 200 } } },
      clearanceMm: { type: "object", additionalProperties: false, required: ["compact", "human", "large"], properties: { compact: { const: 250 }, human: { const: 350 }, large: { const: 600 } } },
      terminal: { type: "object", additionalProperties: false, required: ["widthMm", "depthMm", "columns", "columnSpacingMm", "rowSpacingMm", "originZMm", "centerHeightMm", "interactionDistanceMm"], properties: { widthMm: { const: 1250 }, depthMm: { const: 500 }, columns: { const: 8 }, columnSpacingMm: { const: 1700 }, rowSpacingMm: { const: 2250 }, originZMm: { const: -2400 }, centerHeightMm: { const: 850 }, interactionDistanceMm: { const: 3000 } } },
      limits: { type: "object", additionalProperties: false, required: ["maxCandidatesPerItem", "maxSearchStates"], properties: { maxCandidatesPerItem: { const: 256 }, maxSearchStates: { const: 100_000 } } },
      tolerances: { type: "object", additionalProperties: false, required: ["floorContactMm", "pathEndpointMm"], properties: { floorContactMm: { const: 20 }, pathEndpointMm: { const: 100 } } },
    } },
    zoneSeed: { type: "object", additionalProperties: false, required: ["zoneId", "floorAnchorId"], properties: { zoneId: { $ref: "#/$defs/id" }, floorAnchorId: { $ref: "#/$defs/id" } } },
    zoneDomain: { type: "object", additionalProperties: false, required: ["zoneId", "componentIndex", "floorAnchorIds"], properties: { zoneId: { $ref: "#/$defs/id" }, componentIndex: { $ref: "#/$defs/index" }, floorAnchorIds: { type: "array", minItems: 1, uniqueItems: true, items: { $ref: "#/$defs/id" } } } },
    navigation: { type: "object", additionalProperties: false, required: ["componentIndex", "zoneSeeds", "zoneDomains"], properties: { componentIndex: { $ref: "#/$defs/index" }, zoneSeeds: { type: "array", minItems: 1, maxItems: 4, items: { $ref: "#/$defs/zoneSeed" } }, zoneDomains: { type: "array", minItems: 1, maxItems: 4, items: { $ref: "#/$defs/zoneDomain" } } } },
    placement: { type: "object", additionalProperties: false, required: ["placementId", "anchorKind", "anchorId", "positionMm", "rotationMilliDegrees", "footprint", "proof"], properties: {
      placementId: { $ref: "#/$defs/id" }, anchorKind: { enum: ["floor", "wall"] }, anchorId: { $ref: "#/$defs/id" }, positionMm: { $ref: "#/$defs/coordinate" }, rotationMilliDegrees: { $ref: "#/$defs/rotation" }, footprint: { $ref: "#/$defs/footprint" },
      proof: { type: "object", additionalProperties: false, required: ["supportVerified", "clearanceVerified", "nonOverlapping"], properties: { supportVerified: { const: true }, clearanceVerified: { const: true }, nonOverlapping: { const: true } } },
    } },
    playerSpawn: { type: "object", additionalProperties: false, required: ["floorAnchorId", "positionMm", "yawMilliDegrees"], properties: { floorAnchorId: { $ref: "#/$defs/id" }, positionMm: { $ref: "#/$defs/coordinate" }, yawMilliDegrees: { type: "integer", minimum: -180_000, maximum: 180_000 } } },
    terminalSupport: { type: "object", additionalProperties: false, required: ["floorAnchorId", "baseHeightMm"], properties: { floorAnchorId: { $ref: "#/$defs/id" }, baseHeightMm: { type: "integer", minimum: -1_000_000, maximum: 1_000_000 } } },
    actionTerminal: { type: "object", additionalProperties: false, required: ["floorAnchorId", "approachFloorAnchorId", "positionMm", "yawMilliDegrees", "actionCount", "footprint", "terminalSupports"], properties: { floorAnchorId: { $ref: "#/$defs/id" }, approachFloorAnchorId: { $ref: "#/$defs/id" }, positionMm: { $ref: "#/$defs/coordinate" }, yawMilliDegrees: { type: "integer", minimum: -180_000, maximum: 180_000 }, actionCount: { type: "integer", minimum: 0, maximum: 64 }, footprint: { type: "object", additionalProperties: false, required: ["columns", "widthMm", "depthMm", "layoutWidthMm", "layoutDepthMm", "layoutCenterOffsetMm"], properties: { columns: { type: "integer", minimum: 1, maximum: 8 }, widthMm: { const: 1250 }, depthMm: { const: 500 }, layoutWidthMm: { type: "integer", minimum: 1250, maximum: 13_150 }, layoutDepthMm: { type: "integer", minimum: 500, maximum: 142_250 }, layoutCenterOffsetMm: { type: "array", minItems: 2, maxItems: 2, items: { type: "integer", minimum: -100_000, maximum: 100_000 } } } }, terminalSupports: { type: "array", maxItems: 64, items: { $ref: "#/$defs/terminalSupport" } } } },
    nodeContext: { type: "object", additionalProperties: false, required: ["nodeId", "zoneId", "visiblePlacementIds", "playerSpawn", "actionTerminal", "approachPathFloorAnchorIds"], properties: { nodeId: { $ref: "#/$defs/id" }, zoneId: { $ref: "#/$defs/id" }, visiblePlacementIds: { type: "array", uniqueItems: true, maxItems: 6, items: { $ref: "#/$defs/id" } }, playerSpawn: { $ref: "#/$defs/playerSpawn" }, actionTerminal: { $ref: "#/$defs/actionTerminal" }, approachPathFloorAnchorIds: { type: "array", minItems: 1, uniqueItems: true, items: { $ref: "#/$defs/id" } } } },
    metrics: { type: "object", additionalProperties: false, required: ["candidateCount", "expandedStates"], properties: { candidateCount: { type: "integer", minimum: 1, maximum: 5632 }, expandedStates: { type: "integer", minimum: 1, maximum: 100_000 } } },
    proof: { type: "object", additionalProperties: false, required: ["allHardConstraintsSatisfied", "singleNavigationComponent", "allNodeApproachesReachable"], properties: { allHardConstraintsSatisfied: { const: true }, singleNavigationComponent: { const: true }, allNodeApproachesReachable: { const: true } } },
  },
};
export const PROTOTYPE_SPATIAL_SOLUTION_SCHEMA = deepFreeze(schema);
