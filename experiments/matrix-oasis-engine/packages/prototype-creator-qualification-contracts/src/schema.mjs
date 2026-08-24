export const PROTOTYPE_CREATOR_QUALIFICATION_FORMAT =
  "matrix-oasis.prototype-creator-qualification";
export const PROTOTYPE_CREATOR_QUALIFICATION_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_CREATOR_QUALIFICATION_CANONICALIZATION =
  "matrix-oasis.canonical-json/1";
export const PROTOTYPE_CREATOR_QUALIFICATION_PROFILE =
  "matrix-oasis.creator-solved-evidence/1";
export const PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_PROFILE =
  "matrix-oasis.runtime-replay/1";

export const PROTOTYPE_CREATOR_QUALIFICATION_LIMITS = Object.freeze({
  documentBytes: 256 * 1024,
  documentDepth: 64,
  maxReplays: 32,
  maxScreenshots: 512,
  requiredVideos: 1,
  requiredPerformanceSamples: 300,
  minimumMedianFpsMilli: 30_000,
});

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  for (const child of Object.values(value)) {
    deepFreeze(child);
  }
  return Object.freeze(value);
}

const hash = {
  type: "string",
  pattern: "^sha256:[0-9a-f]{64}$",
};

const hashes = {
  type: "object",
  additionalProperties: false,
  required: [
    "runtimePackSha256",
    "runtimeReceiptSha256",
    "spatialIntentSha256",
    "environmentFactsSha256",
    "assetBundleSha256",
    "spatialSolutionSha256",
    "spatialVerificationSha256",
    "replayPlanSha256",
    "runtimeEvidenceSha256",
  ],
  properties: {
    runtimePackSha256: hash,
    runtimeReceiptSha256: hash,
    spatialIntentSha256: hash,
    environmentFactsSha256: hash,
    assetBundleSha256: hash,
    spatialSolutionSha256: hash,
    spatialVerificationSha256: hash,
    replayPlanSha256: hash,
    runtimeEvidenceSha256: hash,
  },
};

export const PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA = deepFreeze({
  $id: "urn:matrix-oasis:prototype-creator-qualification:0.1.0",
  type: "object",
  additionalProperties: false,
  required: [
    "format",
    "formatVersion",
    "canonicalization",
    "profile",
    "status",
    "promptSha256",
    "model",
    "sourceRunId",
    "hashes",
    "toolchain",
    "evidence",
  ],
  properties: {
    format: { const: PROTOTYPE_CREATOR_QUALIFICATION_FORMAT },
    formatVersion: {
      const: PROTOTYPE_CREATOR_QUALIFICATION_FORMAT_VERSION,
    },
    canonicalization: {
      const: PROTOTYPE_CREATOR_QUALIFICATION_CANONICALIZATION,
    },
    profile: { const: PROTOTYPE_CREATOR_QUALIFICATION_PROFILE },
    status: { const: "qualified" },
    promptSha256: hash,
    model: {
      type: "string",
      minLength: 1,
      maxLength: 128,
      pattern: "^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    },
    sourceRunId: {
      type: "string",
      pattern: "^[0-9a-f]{64}-[0-9a-f]{64}$",
    },
    hashes,
    toolchain: {
      type: "object",
      additionalProperties: false,
      required: ["godotVersion", "renderer", "evidenceProfile"],
      properties: {
        godotVersion: { const: "4.6.3" },
        renderer: { const: "forward_plus" },
        evidenceProfile: {
          const: PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_PROFILE,
        },
      },
    },
    evidence: {
      type: "object",
      additionalProperties: false,
      required: [
        "runId",
        "attempt",
        "replayCount",
        "screenshotCount",
        "videoCount",
        "sampleCount",
        "medianFrameMicros",
        "medianFpsMilli",
      ],
      properties: {
        runId: { type: "string", pattern: "^[0-9a-f]{64}$" },
        attempt: { type: "integer", minimum: 0, maximum: 2 },
        replayCount: { type: "integer", minimum: 1, maximum: 32 },
        screenshotCount: { type: "integer", minimum: 1, maximum: 512 },
        videoCount: { const: 1 },
        sampleCount: { const: 300 },
        medianFrameMicros: {
          type: "integer",
          minimum: 1,
          maximum: 10_000_000,
        },
        medianFpsMilli: {
          type: "integer",
          minimum: 30_000,
          maximum: 1_000_000,
        },
      },
    },
  },
});
