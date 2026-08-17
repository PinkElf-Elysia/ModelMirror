export type PrototypeSpatialPlanningPhase = "parse" | "schema" | "semantic" | "integrity";

export interface PrototypeSpatialPlanningDiagnostic {
  readonly phase: PrototypeSpatialPlanningPhase;
  readonly severity: "error";
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface PrototypeSpatialPlanningValidationReport {
  readonly reportVersion: 1;
  readonly valid: boolean;
  readonly diagnostics: readonly PrototypeSpatialPlanningDiagnostic[];
}

export type SpatialVector3Mm = readonly [number, number, number];
export type SpatialNormalMicros = readonly [number, number, number];

export interface PrototypeSpatialIntent {
  readonly format: "matrix-oasis.prototype-spatial-intent";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly scene: { readonly id: string; readonly contentVersion: string };
  readonly blueprint: { readonly format: "matrix-oasis.scene-blueprint"; readonly formatVersion: "0.1.0"; readonly canonicalSha256: string };
  readonly runtime: { readonly format: "matrix-oasis.runtime-game-pack"; readonly formatVersion: "0.1.0"; readonly id: string; readonly contentVersion: string; readonly sourceSha256: string; readonly artifactSha256: string };
  readonly assetBundle: { readonly format: "matrix-oasis.prototype-asset-bundle"; readonly formatVersion: "0.1.0"; readonly canonicalSha256: string };
  readonly zones: readonly { readonly id: string; readonly adjacentZoneIds: readonly string[] }[];
  readonly placements: readonly PrototypeSpatialIntentPlacement[];
  readonly nodeContexts: readonly { readonly nodeId: string; readonly zoneId: string; readonly visiblePlacementIds: readonly string[]; readonly requiresPlayerSpawn: true; readonly requiresActionTerminal: true }[];
}

export interface PrototypeSpatialIntentPlacement {
  readonly id: string;
  readonly assetBriefId: string;
  readonly zoneId: string;
  readonly support: "floor" | "wall";
  readonly anchor: "free" | "center" | "edge";
  readonly facing: { readonly kind: "none" } | { readonly kind: "zone-center" } | { readonly kind: "placement"; readonly placementId: string };
  readonly near: readonly { readonly placementId: string; readonly distanceMm: number }[];
  readonly separate: readonly { readonly placementId: string; readonly distanceMm: number }[];
  readonly clearanceClass: "compact" | "human" | "large";
}

export interface PrototypeEnvironmentFacts {
  readonly format: "matrix-oasis.prototype-environment-facts";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly source: PrototypeEnvironmentFactsSource;
  readonly coordinateSystem: { readonly handedness: "right"; readonly upAxis: "Y"; readonly unit: "millimeter"; readonly eulerOrder: "YXZ" };
  readonly analysisProfile: { readonly playerRadiusMm: 350; readonly playerHeightMm: 1800; readonly floorSnapMm: 200; readonly maxSlopeMilliDegrees: 45000 };
  readonly environmentBounds: PrototypeSpatialBounds;
  readonly navigationMesh: PrototypeNavigationMeshFacts;
  readonly floorAnchors: readonly PrototypeFloorAnchorFact[];
  readonly wallAnchors: readonly PrototypeWallAnchorFact[];
}

export interface PrototypeEnvironmentFactsSource {
  readonly scene: { readonly id: string; readonly contentVersion: string };
  readonly blueprint: { readonly format: "matrix-oasis.scene-blueprint"; readonly formatVersion: "0.1.0"; readonly canonicalSha256: string };
  readonly runtime: { readonly format: "matrix-oasis.runtime-game-pack"; readonly formatVersion: "0.1.0"; readonly id: string; readonly contentVersion: string; readonly sourceSha256: string; readonly artifactSha256: string };
  readonly spatialEnvironmentBundle: { readonly format: "matrix-oasis.prototype-spatial-environment-bundle"; readonly formatVersion: "0.1.0"; readonly canonicalSha256: string };
  readonly environmentBundleSha256: string;
  readonly collider: { readonly format: "glb"; readonly byteLength: number; readonly sha256: string };
  readonly calibration: { readonly coordinateTransform: "spz-raw-ply-to-godot-v1"; readonly metricScaleMicros: number; readonly groundPlaneOffsetMm: number; readonly godotTranslationMm: SpatialVector3Mm; readonly godotRotationMilliDegrees: SpatialVector3Mm };
}

export interface PrototypeSpatialBounds { readonly minimumMm: SpatialVector3Mm; readonly maximumMm: SpatialVector3Mm }
export interface PrototypeNavigationMeshFacts {
  readonly verticesMm: readonly SpatialVector3Mm[];
  readonly polygons: readonly { readonly vertexIndices: readonly number[]; readonly componentIndex: number }[];
  readonly components: readonly { readonly index: number; readonly polygonIndices: readonly number[]; readonly bounds: PrototypeSpatialBounds }[];
}
export interface PrototypeFloorAnchorFact { readonly id: string; readonly positionMm: SpatialVector3Mm; readonly normalMicros: SpatialNormalMicros; readonly clearanceRadiusMm: number; readonly clearanceHeightMm: number; readonly ceilingHeightMm: number; readonly componentIndex: number; readonly polygonIndex: number; readonly capsuleClearanceVerified: true }
export interface PrototypeWallAnchorFact { readonly id: string; readonly positionMm: SpatialVector3Mm; readonly normalMicros: SpatialNormalMicros; readonly availableWidthMm: number; readonly availableHeightMm: number; readonly nearestFloorAnchorId: string }

export declare const PROTOTYPE_SPATIAL_INTENT_FORMAT: "matrix-oasis.prototype-spatial-intent";
export declare const PROTOTYPE_ENVIRONMENT_FACTS_FORMAT: "matrix-oasis.prototype-environment-facts";
export declare const PROTOTYPE_SPATIAL_PLANNING_FORMAT_VERSION: "0.1.0";
export declare const PROTOTYPE_SPATIAL_PLANNING_CANONICALIZATION: "matrix-oasis.canonical-json/1";
export declare const PROTOTYPE_SPATIAL_INTENT_SCHEMA: Readonly<Record<string, unknown>>;
export declare const PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA: Readonly<Record<string, unknown>>;
export declare const PROTOTYPE_SPATIAL_PLANNING_LIMITS: Readonly<{
  documentDepth: 256;
  intentBytes: 2097152;
  factsBytes: 16777216;
  zones: 16;
  placements: 128;
  nodeContexts: 4096;
  constraintsPerPlacement: 32;
  navigationVertices: 200000;
  navigationPolygons: 200000;
  navigationComponents: 4096;
  floorAnchors: 65536;
  wallAnchors: 65536;
}>;

export declare class PrototypeSpatialPlanningContractOperationalError extends Error {
  readonly code: "PROTOTYPE_SPATIAL_PLANNING_CONTRACT_INTERNAL_ERROR";
}

export declare function validatePrototypeSpatialIntentJson(text: string): PrototypeSpatialPlanningValidationReport;
export declare function validatePrototypeEnvironmentFactsJson(text: string): PrototypeSpatialPlanningValidationReport;
