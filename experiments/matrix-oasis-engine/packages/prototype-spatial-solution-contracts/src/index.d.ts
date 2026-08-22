export type PrototypeSpatialSolutionPhase = "parse" | "schema" | "semantic" | "integrity";
export interface PrototypeSpatialSolutionDiagnostic { readonly phase: PrototypeSpatialSolutionPhase; readonly severity: "error"; readonly code: string; readonly path: string; readonly message: string }
export interface PrototypeSpatialSolutionValidationReport { readonly reportVersion: 1; readonly valid: boolean; readonly diagnostics: readonly PrototypeSpatialSolutionDiagnostic[] }
export type PrototypeSpatialVector3Mm = readonly [number, number, number];
export interface PrototypeSpatialSolution {
  readonly format: "matrix-oasis.prototype-spatial-solution";
  readonly formatVersion: "0.1.0";
  readonly canonicalization: "matrix-oasis.canonical-json/1";
  readonly source: Readonly<{
    spatialIntent: Readonly<{ format: "matrix-oasis.prototype-spatial-intent"; formatVersion: "0.1.0"; canonicalSha256: string }>;
    environmentFacts: Readonly<{ format: "matrix-oasis.prototype-environment-facts"; formatVersion: "0.1.0"; canonicalSha256: string }>;
    runtime: Readonly<{ format: "matrix-oasis.runtime-game-pack"; formatVersion: "0.1.0"; id: string; contentVersion: string; sourceSha256: string; artifactSha256: string }>;
    runtimeReceiptSha256: string;
    assetBundle: Readonly<{ format: "matrix-oasis.prototype-asset-bundle"; formatVersion: "0.1.0"; canonicalSha256: string }>;
    analysisTransformSource: Readonly<
      { profile: "spatial-assembly-collider-v1"; format: "matrix-oasis.prototype-spatial-assembly"; formatVersion: "0.1.0"; canonicalSha256: string } |
      { profile: "spatial-environment-calibration-v1"; format: "matrix-oasis.prototype-spatial-environment-bundle"; formatVersion: "0.1.0"; canonicalSha256: string }
    >;
  }>;
  readonly profile: Readonly<Record<string, unknown>>;
  readonly navigation: Readonly<{ componentIndex: number; zoneSeeds: readonly Readonly<{ zoneId: string; floorAnchorId: string }>[]; zoneDomains: readonly Readonly<{ zoneId: string; componentIndex: number; floorAnchorIds: readonly string[] }>[] }>;
  readonly placements: readonly Readonly<{ placementId: string; anchorKind: "floor" | "wall"; anchorId: string; positionMm: PrototypeSpatialVector3Mm; rotationMilliDegrees: PrototypeSpatialVector3Mm; footprint: Readonly<{ widthMm: number; heightMm: number; depthMm: number }>; proof: Readonly<{ supportVerified: true; clearanceVerified: true; nonOverlapping: true }> }>[];
  readonly nodeContexts: readonly Readonly<{ nodeId: string; zoneId: string; visiblePlacementIds: readonly string[]; playerSpawn: Readonly<{ floorAnchorId: string; positionMm: PrototypeSpatialVector3Mm; yawMilliDegrees: number }>; actionTerminal: Readonly<{ floorAnchorId: string; approachFloorAnchorId: string; positionMm: PrototypeSpatialVector3Mm; yawMilliDegrees: number; actionCount: number; footprint: Readonly<{ columns: number; widthMm: 1250; depthMm: 500; layoutWidthMm: number; layoutDepthMm: number; layoutCenterOffsetMm: readonly [number, number] }>; terminalSupports: readonly Readonly<{ floorAnchorId: string; baseHeightMm: number }>[] }>; approachPathFloorAnchorIds: readonly string[] }>[];
  readonly metrics: Readonly<{ candidateCount: number; expandedStates: number }>;
  readonly proof: Readonly<{ allHardConstraintsSatisfied: true; singleNavigationComponent: true; allNodeApproachesReachable: true }>;
}
export declare const PROTOTYPE_SPATIAL_SOLUTION_FORMAT: "matrix-oasis.prototype-spatial-solution";
export declare const PROTOTYPE_SPATIAL_SOLUTION_FORMAT_VERSION: "0.1.0";
export declare const PROTOTYPE_SPATIAL_SOLUTION_CANONICALIZATION: "matrix-oasis.canonical-json/1";
export declare const PROTOTYPE_SPATIAL_SOLUTION_SCHEMA: Readonly<Record<string, unknown>>;
export declare const PROTOTYPE_SPATIAL_SOLUTION_PROFILE: Readonly<{
  id: "matrix-oasis.spatial-solver/1"; maxZones: 4; maxPlacements: 6; maxNodeContexts: 16; maxActionsPerNode: 64;
  maxCandidatesPerItem: 256; maxSearchStates: 100000; playerRadiusMm: 350; playerHeightMm: 1800; playerEyeHeightMm: 1475; floorSnapMm: 200;
  compactClearanceMm: 250; humanClearanceMm: 350; largeClearanceMm: 600; terminalWidthMm: 1250; terminalDepthMm: 500;
  terminalColumns: 8; terminalColumnSpacingMm: 1700; terminalRowSpacingMm: 2250; terminalOriginZMm: -2400; terminalCenterHeightMm: 850; interactionDistanceMm: 3000;
  floorContactToleranceMm: 20; pathEndpointToleranceMm: 100;
}>;
export declare const PROTOTYPE_SPATIAL_SOLUTION_LIMITS: Readonly<{ documentDepth: 256; documentBytes: 16777216; coordinateMm: 1000000; rotationMilliDegrees: 360000; footprintMm: 100000 }>;
export declare class PrototypeSpatialSolutionContractOperationalError extends Error { readonly code: "PROTOTYPE_SPATIAL_SOLUTION_CONTRACT_INTERNAL_ERROR" }
export declare function validatePrototypeSpatialSolutionJson(text: string): PrototypeSpatialSolutionValidationReport;
