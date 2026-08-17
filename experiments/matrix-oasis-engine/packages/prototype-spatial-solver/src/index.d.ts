import type { PrototypeSpatialIntent } from "@matrix-oasis/prototype-spatial-planning-contracts";
export interface PrototypeSpatialIntentSynthesisRequest { readonly sceneBlueprintJson: string; readonly runtimeGamePackJson: string; readonly runtimeReceiptJson: string; readonly assetBundleJson: string }
export interface PrototypeSpatialSolverDiagnostic { readonly phase: "synthesis" | "solver"; readonly severity: "error"; readonly code: string; readonly path: string; readonly message: string }
export type PrototypeSpatialIntentSynthesisResult = Readonly<{ ok: true; spatialIntent: PrototypeSpatialIntent; canonicalSpatialIntentJson: string }> | Readonly<{ ok: false; diagnostics: readonly PrototypeSpatialSolverDiagnostic[] }>;
export declare const PROTOTYPE_SPATIAL_INTENT_SYNTHESIS_PROFILE: Readonly<{ id: "matrix-oasis.spatial-intent-synthesis/1"; maxZones: 4; maxPlacements: 6; maxNodeContexts: 16; maxActionsPerNode: 64; largeFootprintThresholdMm: 1200 }>;
export declare class PrototypeSpatialSolverOperationalError extends Error { readonly code: "PROTOTYPE_SPATIAL_SOLVER_INTERNAL_ERROR" }
export declare function synthesizePrototypeSpatialIntent(request: PrototypeSpatialIntentSynthesisRequest): Promise<PrototypeSpatialIntentSynthesisResult>;
export declare function solvePrototypeSpatialLayout(request: unknown): Promise<never>;
