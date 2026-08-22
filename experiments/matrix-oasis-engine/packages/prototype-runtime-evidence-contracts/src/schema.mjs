export const PROTOTYPE_RUNTIME_REPLAY_PLAN_FORMAT = "matrix-oasis.prototype-runtime-replay-plan";
export const PROTOTYPE_RUNTIME_EVIDENCE_FORMAT = "matrix-oasis.prototype-runtime-evidence";
export const PROTOTYPE_RUNTIME_EVIDENCE_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_RUNTIME_EVIDENCE_CANONICALIZATION = "matrix-oasis.canonical-json/1";
export const PROTOTYPE_RUNTIME_REPLAY_PROFILE = Object.freeze({
  id: "matrix-oasis.runtime-replay/1",
  maxReplays: 32,
  maxActionsPerReplay: 256,
  maxSemanticStates: 100_000,
  requiredPerformanceSamples: 300,
  minimumMedianFpsMilli: 30_000,
});
export const PROTOTYPE_RUNTIME_EVIDENCE_LIMITS = Object.freeze({ documentBytes: 16 * 1024 * 1024, documentDepth: 256, maxCheckpoints: 8192, maxScreenshots: 512, maxVideos: 32 });

function deepFreeze(value) { if (!value || typeof value !== "object" || Object.isFrozen(value)) return value; for (const child of Object.values(value)) deepFreeze(child); return Object.freeze(value); }
const id = { type: "string", minLength: 1, maxLength: 96, pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$" };
const hash = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
const count = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER };
const position = { type: "array", minItems: 3, maxItems: 3, items: { type: "integer", minimum: -1_000_000, maximum: 1_000_000 } };
const identity = { type: "object", additionalProperties: false, required: ["runtimePackSha256", "runtimeReceiptSha256", "environmentFactsSha256", "spatialIntentSha256", "assetBundleSha256", "spatialSolutionSha256", "spatialVerificationSha256"], properties: { runtimePackSha256: hash, runtimeReceiptSha256: hash, environmentFactsSha256: hash, spatialIntentSha256: hash, assetBundleSha256: hash, spatialSolutionSha256: hash, spatialVerificationSha256: hash } };
const replayKind = { enum: ["ending", "loop", "node-coverage", "disabled-action", "reset-ending", "reset-active"] };
const replay = { type: "object", additionalProperties: false, required: ["id", "kind", "actionIds", "probeActionId", "targetId", "resetAfter", "expectedLocationIds"], properties: { id, kind: replayKind, actionIds: { type: "array", maxItems: 256, items: id }, probeActionId: { oneOf: [{ type: "null" }, id] }, targetId: { oneOf: [{ type: "null" }, id] }, resetAfter: { type: "boolean" }, expectedLocationIds: { type: "array", minItems: 1, maxItems: 257, items: id } } };
const checkpoint = { type: "object", additionalProperties: false, required: ["sequence", "locationKind", "locationId", "stepCount", "actionId", "playerPositionMm", "floorDistanceMm", "capsuleClear", "navigationPathComplete", "focusedActionId", "interactionDistanceMm", "visiblePlacementIds"], properties: { sequence: count, locationKind: { enum: ["node", "ending"] }, locationId: id, stepCount: count, actionId: { oneOf: [{ type: "null" }, id] }, playerPositionMm: position, floorDistanceMm: { type: "integer", minimum: -100_000, maximum: 100_000 }, capsuleClear: { type: "boolean" }, navigationPathComplete: { type: "boolean" }, focusedActionId: { oneOf: [{ type: "null" }, id] }, interactionDistanceMm: { oneOf: [{ type: "null" }, { type: "integer", minimum: 0, maximum: 100_000 }] }, visiblePlacementIds: { type: "array", maxItems: 6, uniqueItems: true, items: id } } };

export const PROTOTYPE_RUNTIME_REPLAY_PLAN_SCHEMA = deepFreeze({
  $id: "urn:matrix-oasis:prototype-runtime-replay-plan:0.1.0", type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "identity", "profile", "coverage", "replays"],
  properties: {
    format: { const: PROTOTYPE_RUNTIME_REPLAY_PLAN_FORMAT }, formatVersion: { const: PROTOTYPE_RUNTIME_EVIDENCE_FORMAT_VERSION }, canonicalization: { const: PROTOTYPE_RUNTIME_EVIDENCE_CANONICALIZATION }, identity,
    profile: { type: "object", additionalProperties: false, required: ["id", "maxReplays", "maxActionsPerReplay", "maxSemanticStates"], properties: { id: { const: "matrix-oasis.runtime-replay/1" }, maxReplays: { const: 32 }, maxActionsPerReplay: { const: 256 }, maxSemanticStates: { const: 100_000 } } },
    coverage: { type: "object", additionalProperties: false, required: ["declaredEndingCount", "reachableEndingCount", "activeNodeCount", "coveredNodeCount", "loop", "disabledAction"], properties: { declaredEndingCount: count, reachableEndingCount: count, activeNodeCount: count, coveredNodeCount: count, loop: { enum: ["covered", "not-applicable"] }, disabledAction: { enum: ["covered", "not-applicable"] } } },
    replays: { type: "array", minItems: 1, maxItems: 32, items: replay },
  },
});

export const PROTOTYPE_RUNTIME_EVIDENCE_SCHEMA = deepFreeze({
  $id: "urn:matrix-oasis:prototype-runtime-evidence:0.1.0", type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "replayPlanSha256", "identity", "attempt", "status", "observations", "performance", "media", "repairs"],
  properties: {
    format: { const: PROTOTYPE_RUNTIME_EVIDENCE_FORMAT }, formatVersion: { const: PROTOTYPE_RUNTIME_EVIDENCE_FORMAT_VERSION }, canonicalization: { const: PROTOTYPE_RUNTIME_EVIDENCE_CANONICALIZATION }, replayPlanSha256: hash, identity,
    attempt: { type: "integer", minimum: 0, maximum: 2 }, status: { enum: ["passed", "failed"] },
    observations: { type: "array", minItems: 1, maxItems: 32, items: { type: "object", additionalProperties: false, required: ["replayId", "kind", "outcome", "checkpoints"], properties: { replayId: id, kind: replayKind, outcome: { enum: ["passed", "failed"] }, checkpoints: { type: "array", minItems: 1, maxItems: 8192, items: checkpoint } } } },
    performance: { type: "object", additionalProperties: false, required: ["sampleCount", "medianFrameMicros", "medianFpsMilli"], properties: { sampleCount: { const: 300 }, medianFrameMicros: { type: "integer", minimum: 1, maximum: 10_000_000 }, medianFpsMilli: { type: "integer", minimum: 1, maximum: 1_000_000 } } },
    media: { type: "object", additionalProperties: false, required: ["screenshots", "videos"], properties: {
      screenshots: { type: "array", maxItems: 512, items: { type: "object", additionalProperties: false, required: ["replayId", "locationId", "width", "height", "sha256"], properties: { replayId: id, locationId: id, width: { type: "integer", minimum: 1, maximum: 16384 }, height: { type: "integer", minimum: 1, maximum: 16384 }, sha256: hash } } },
      videos: { type: "array", maxItems: 1, items: { type: "object", additionalProperties: false, required: ["scope", "frameRate", "frameCount", "sha256"], properties: { scope: { const: "full-run" }, frameRate: { const: 30 }, frameCount: { type: "integer", minimum: 1, maximum: 1_000_000 }, sha256: hash } } },
    } },
    repairs: { type: "array", maxItems: 2, items: { type: "object", additionalProperties: false, required: ["round", "kind", "candidateKeySha256", "diagnosticCode"], properties: { round: { type: "integer", minimum: 1, maximum: 2 }, kind: { enum: ["placement", "station", "terminal"] }, candidateKeySha256: hash, diagnosticCode: { enum: ["R15_PLACEMENT_RUNTIME_INVALID", "R15_STATION_RUNTIME_INVALID", "R15_TERMINAL_RUNTIME_INVALID"] } } } },
  },
});
