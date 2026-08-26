const HASH = "^[0-9a-f]{64}$";
const GIT_SHA = "^[0-9a-f]{40}$";
const SAFE_ID = "^[a-z][a-z0-9-]{0,63}$";
const CODE = "^[A-Z][A-Z0-9_]{2,95}$";
const JSON_SCHEMA_URI = ["https:", "", "json-schema.org", "draft", "2020-12", "schema"].join("/");

const string = (maxLength, pattern) => ({ type: "string", minLength: 1, maxLength, ...(pattern ? { pattern } : {}) });
const closed = (required, properties) => ({ type: "object", additionalProperties: false, required, properties });
const nullable = (schema) => ({ anyOf: [schema, { type: "null" }] });

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(deepFreeze);
  return Object.freeze(value);
}

export const V2_LANDSCAPE_LIMITS = deepFreeze({
  documentBytes: 4 * 1024 * 1024,
  documentDepth: 64,
  lanes: 8,
  candidates: 256,
  decisions: 1024,
  roadmapRounds: 7,
});

export const V2_LANES = deepFreeze([
  "npc-orchestration",
  "memory-relationships",
  "dynamic-events",
  "godot-behavior",
  "dialogue-presentation",
  "character-animation",
  "evaluation-observability",
  "creator-commercial-benchmark",
]);

export const V2_SCORE_LIMITS = deepFreeze({
  authorityCompatibility: 20,
  userCommercialValue: 15,
  standaloneIntegration: 15,
  determinismEvaluation: 10,
  securityFailClosed: 10,
  licenseMaintenanceSource: 10,
  performanceLatencyCost: 10,
  experienceVisualPotential: 5,
  functionality: 5,
});

export const V2_CLASS_GATES = deepFreeze({
  "embedded-godot": ["license", "source-identity", "authority-boundary", "execution-isolation", "runtime-compatibility", "determinism"],
  service: ["license", "source-identity", "authority-boundary", "execution-isolation", "ledger-rebuild", "fail-closed"],
  asset: ["license", "source-identity", "import-identity", "runtime-compatibility", "performance"],
  commercial: ["public-evidence"],
  internal: ["authority-boundary", "determinism", "fail-closed", "runtime-compatibility"],
});

const laneId = { enum: V2_LANES };
const hash = string(64, HASH);
const gitSha = string(40, GIT_SHA);
const safeId = string(64, SAFE_ID);
const code = string(96, CODE);

const sourceSchema = closed(
  ["kind", "location", "commit", "gitTreeSha1", "archiveSha256", "identitySha256"],
  {
    kind: { enum: ["git-repository", "source-archive", "internal-baseline", "public-documentation"] },
    location: closed(
      ["host", "path"],
      {
        host: string(253, "^(internal|[a-z0-9.-]+\\.[a-z]{2,})$"),
        path: string(256, "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$))[A-Za-z0-9._/-]+$"),
      },
    ),
    commit: nullable(gitSha),
    gitTreeSha1: nullable(gitSha),
    archiveSha256: nullable(hash),
    identitySha256: hash,
  },
);

const candidateSchema = closed(
  ["id", "name", "candidateType", "newSinceR17", "laneIds", "source", "license", "surface", "maintenance", "staticExclusion"],
  {
    id: safeId,
    name: string(128),
    candidateType: { enum: ["open-source", "internal-baseline", "commercial-benchmark", "public-asset"] },
    newSinceR17: { type: "boolean" },
    laneIds: { type: "array", minItems: 1, maxItems: 8, uniqueItems: true, items: laneId },
    source: sourceSchema,
    license: closed(
      ["spdx", "reuseAllowed", "closureStatus", "evidenceSha256"],
      {
        spdx: string(64, "^[A-Za-z0-9.+-]+$"),
        reuseAllowed: { type: "boolean" },
        closureStatus: { enum: ["approved", "reference-only", "unknown"] },
        evidenceSha256: hash,
      },
    ),
    surface: closed(
      ["runtimeClass", "requiresContainer", "lifecycleScripts", "nativeBinaries", "defaultNetwork", "externalServices", "dependencyCount"],
      {
        runtimeClass: { enum: ["embedded-godot", "service", "asset", "commercial", "internal"] },
        requiresContainer: { type: "boolean" },
        lifecycleScripts: { type: "integer", minimum: 0, maximum: 64 },
        nativeBinaries: { type: "integer", minimum: 0, maximum: 64 },
        defaultNetwork: { enum: ["none", "loopback", "external", "unknown"] },
        externalServices: { type: "integer", minimum: 0, maximum: 32 },
        dependencyCount: { type: "integer", minimum: 0, maximum: 65535 },
      },
    ),
    maintenance: closed(
      ["state", "lastReleaseYearMonth", "evidenceSha256"],
      {
        state: { enum: ["active", "maintenance", "archived", "unknown"] },
        lastReleaseYearMonth: nullable(string(7, "^[0-9]{4}-(0[1-9]|1[0-2])$")),
        evidenceSha256: hash,
      },
    ),
    staticExclusion: closed(
      ["excluded", "code"],
      { excluded: { type: "boolean" }, code: nullable(code) },
    ),
  },
);

export const V2_CANDIDATE_CATALOG_SCHEMA = deepFreeze({
  $schema: JSON_SCHEMA_URI,
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "catalog"],
  properties: {
    format: { const: "matrix-oasis.v2-candidate-catalog" },
    formatVersion: { const: "0.1.0" },
    canonicalization: { const: "matrix-oasis.canonical-json/1" },
    catalog: closed(
      ["querySetSha256", "r17SourceLockSha256", "lanes", "candidates"],
      {
        querySetSha256: hash,
        r17SourceLockSha256: hash,
        lanes: {
          type: "array",
          minItems: 8,
          maxItems: 8,
          items: closed(
            ["id", "title", "executable", "candidateIds"],
            {
              id: laneId,
              title: string(128),
              executable: { type: "boolean" },
              candidateIds: { type: "array", minItems: 6, maxItems: 256, uniqueItems: true, items: safeId },
            },
          ),
        },
        candidates: { type: "array", minItems: 32, maxItems: 256, items: candidateSchema },
      },
    ),
  },
});

const scoreSchema = closed(
  Object.keys(V2_SCORE_LIMITS),
  Object.fromEntries(Object.entries(V2_SCORE_LIMITS).map(([key, maximum]) => [key, { type: "integer", minimum: 0, maximum }])),
);

const gateStatus = { enum: ["pass", "fail", "not-proven", "not-applicable"] };
const decisionSchema = closed(
  ["candidateId", "laneId", "qualificationClass", "executionStatus", "harnessAttribution", "tier", "conclusion", "confidence", "hardGates", "scores", "total", "runtimeSurface", "shortlistRank", "evidenceSha256", "switchConditions", "exclusionCode"],
  {
    candidateId: safeId,
    laneId,
    qualificationClass: { enum: ["embedded-godot", "service", "asset", "commercial", "internal"] },
    executionStatus: { enum: ["not-required", "planned", "executed", "failed", "evidence-gap"] },
    harnessAttribution: { enum: ["candidate", "harness", "unresolved", "not-applicable"] },
    tier: { enum: ["architecture-reference", "executable-shortlist", "integration-recommended"] },
    conclusion: { enum: ["recommended", "backup", "deferred", "rejected"] },
    confidence: { enum: ["high", "medium", "low"] },
    hardGates: {
      type: "array",
      minItems: 1,
      maxItems: 32,
      items: closed(["id", "status", "code"], { id: safeId, status: gateStatus, code: nullable(code) }),
    },
    scores: scoreSchema,
    total: { type: "integer", minimum: 0, maximum: 100 },
    runtimeSurface: closed(
      ["services", "nativeBinaries", "dependencies"],
      {
        services: { type: "integer", minimum: 0, maximum: 32 },
        nativeBinaries: { type: "integer", minimum: 0, maximum: 64 },
        dependencies: { type: "integer", minimum: 0, maximum: 65535 },
      },
    ),
    shortlistRank: nullable({ type: "integer", minimum: 1, maximum: 3 }),
    evidenceSha256: { type: "array", maxItems: 64, uniqueItems: true, items: hash },
    switchConditions: {
      type: "array",
      minItems: 1,
      maxItems: 16,
      items: closed(["code", "observable"], { code, observable: string(256) }),
    },
    exclusionCode: nullable(code),
  },
);

export const V2_DECISION_LANDSCAPE_SCHEMA = deepFreeze({
  $schema: JSON_SCHEMA_URI,
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "catalogSha256", "policy", "decisions"],
  properties: {
    format: { const: "matrix-oasis.v2-decision-landscape" },
    formatVersion: { const: "0.1.0" },
    canonicalization: { const: "matrix-oasis.canonical-json/1" },
    catalogSha256: hash,
    policy: closed(
      ["shortlistMinimumScore", "integrationMinimumScore", "nearTieScoreDelta", "minimumExecutedCandidates", "maximumExecutedCandidates"],
      {
        shortlistMinimumScore: { const: 70 },
        integrationMinimumScore: { const: 80 },
        nearTieScoreDelta: { const: 5 },
        minimumExecutedCandidates: { const: 12 },
        maximumExecutedCandidates: { const: 16 },
      },
    ),
    decisions: { type: "array", minItems: 48, maxItems: 1024, items: decisionSchema },
  },
});

const roadmapRound = closed(
  ["id", "objective", "dependsOn", "entryGates", "exitGates", "prohibited", "rollback"],
  {
    id: { enum: ["R19", "R20", "R21", "R22", "R23", "R24", "R25"] },
    objective: string(512),
    dependsOn: { type: "array", maxItems: 6, uniqueItems: true, items: { enum: ["R18", "R19", "R20", "R21", "R22", "R23", "R24"] } },
    entryGates: { type: "array", minItems: 1, maxItems: 16, uniqueItems: true, items: code },
    exitGates: { type: "array", minItems: 1, maxItems: 16, uniqueItems: true, items: code },
    prohibited: { type: "array", minItems: 1, maxItems: 32, uniqueItems: true, items: code },
    rollback: string(512),
  },
);

export const V2_ROADMAP_SCHEMA = deepFreeze({
  $schema: JSON_SCHEMA_URI,
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "decisionLandscapeSha256", "rounds"],
  properties: {
    format: { const: "matrix-oasis.v2-roadmap" },
    formatVersion: { const: "0.1.0" },
    canonicalization: { const: "matrix-oasis.canonical-json/1" },
    decisionLandscapeSha256: hash,
    rounds: { type: "array", minItems: 7, maxItems: 7, items: roadmapRound },
  },
});
