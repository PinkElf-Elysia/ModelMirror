export type V2LaneId =
  | "npc-orchestration"
  | "memory-relationships"
  | "dynamic-events"
  | "godot-behavior"
  | "dialogue-presentation"
  | "character-animation"
  | "evaluation-observability"
  | "creator-commercial-benchmark";

export type V2EvidenceTier =
  | "architecture-reference"
  | "executable-shortlist"
  | "integration-recommended";

export type V2Disposition = "recommended" | "backup" | "deferred" | "rejected";

export interface V2Diagnostic {
  phase: "parse" | "schema" | "semantic" | "integrity" | "operation";
  severity: "error";
  code: string;
  path: string;
  message: string;
}

export interface V2ValidationReport<T = unknown> {
  reportVersion: 1;
  valid: boolean;
  diagnostics: readonly V2Diagnostic[];
  value?: Readonly<T>;
}

export interface V2TierEvaluation {
  candidateId: string;
  laneId: V2LaneId | "invalid";
  tier: V2EvidenceTier;
  conclusion: V2Disposition;
  total: number;
  evidenceGap: boolean;
  productionGatesPassed: boolean;
  desktopGatesPassed: boolean;
  runtimeSurface: Readonly<{ services: number; nativeBinaries: number; dependencies: number }>;
  switchConditions: readonly unknown[];
}

export declare function validateV2CandidateCatalogJson(text: string): V2ValidationReport;
export declare function validateV2DecisionLandscapeJson(text: string): V2ValidationReport;
export declare function validateV2RoadmapJson(text: string): V2ValidationReport;
export declare function evaluateV2CandidateForTier(
  candidate: unknown,
  evidence: unknown,
  policy?: { shortlistMinimumScore?: number; integrationMinimumScore?: number },
): Readonly<V2TierEvaluation>;
export declare function selectV2LaneShortlist(
  catalog: unknown,
  evidence: readonly unknown[],
  policy?: { shortlistMinimumScore?: number; integrationMinimumScore?: number; maximumPerLane?: number; nearTieScoreDelta?: number },
): readonly Readonly<{ laneId: V2LaneId; candidateIds: readonly string[] }> [];

export declare const V2_CANDIDATE_CATALOG_SCHEMA: Readonly<Record<string, unknown>>;
export declare const V2_CLASS_GATES: Readonly<Record<string, readonly string[]>>;
export declare const V2_DESKTOP_GATES: Readonly<Record<string, readonly string[]>>;
export declare const V2_DECISION_LANDSCAPE_SCHEMA: Readonly<Record<string, unknown>>;
export declare const V2_ROADMAP_SCHEMA: Readonly<Record<string, unknown>>;
export declare const V2_LANES: readonly V2LaneId[];
export declare const V2_SCORE_LIMITS: Readonly<Record<string, number>>;
