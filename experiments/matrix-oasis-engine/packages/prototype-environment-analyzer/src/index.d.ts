import type {
  PrototypeEnvironmentFacts,
  PrototypeSpatialPlanningDiagnostic,
} from "@matrix-oasis/prototype-spatial-planning-contracts";

export interface GodotEnvironmentAnalyzer {
  readonly kind: "matrix-oasis.godot-environment-analyzer/1";
}

export declare class PrototypeEnvironmentAnalyzerOperationalError extends Error {
  readonly code: "PROTOTYPE_SPATIAL_ANALYZER_INTERNAL_ERROR";
}

export declare function createGodotEnvironmentAnalyzer(config: Readonly<{
  godotBin: string;
}>): GodotEnvironmentAnalyzer;

export declare function analyzePrototypeEnvironment(
  request: Readonly<{
    spatialIntentJson: string;
    spatialEnvironmentBundleJson: string;
    spatialEnvironmentFiles: ReadonlyMap<string, Uint8Array>;
  }>,
  analyzer: GodotEnvironmentAnalyzer,
): Promise<
  | Readonly<{
      ok: true;
      facts: PrototypeEnvironmentFacts;
      canonicalFactsJson: string;
      canonicalReportJson: string;
    }>
  | Readonly<{
      ok: false;
      diagnostics: readonly PrototypeSpatialPlanningDiagnostic[];
    }>
>;
