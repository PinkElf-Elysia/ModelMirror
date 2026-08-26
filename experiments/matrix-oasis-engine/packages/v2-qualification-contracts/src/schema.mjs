const HASH = "^[0-9a-f]{64}$";
const GIT_SHA = "^[0-9a-f]{40}$";
const SAFE_ID = "^[a-z][a-z0-9-]{0,63}$";
const SAFE_PATH = "^(?!/)(?![A-Za-z]:)(?!.*\\\\)(?!.*(?:^|/)\\.\\.(?:/|$))[A-Za-z0-9._/-]+$";
const JSON_SCHEMA_URI = ["https:", "", "json-schema.org", "draft", "2020-12", "schema"].join("/");
const GITHUB_REPOSITORY = "^" + "https:" + "\\/\\/github\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$";

const string = (maxLength, pattern) => ({ type: "string", minLength: 1, maxLength, ...(pattern ? { pattern } : {}) });
const closed = (required, properties) => ({ type: "object", additionalProperties: false, required, properties });

export const V2_QUALIFICATION_LIMITS = Object.freeze({ documentBytes: 1024 * 1024, documentDepth: 64 });

export const V2_CANDIDATE_LOCK_SCHEMA = Object.freeze({
  $schema: JSON_SCHEMA_URI,
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "candidate", "executionPolicy"],
  properties: {
    format: { const: "matrix-oasis.v2-candidate-lock" },
    formatVersion: { const: "0.1.0" },
    canonicalization: { const: "matrix-oasis.canonical-json/1" },
    candidate: closed(
      ["id", "lane", "repository", "tag", "commit", "gitTreeSha1", "treeListSha256", "sourceArchiveSha256", "license", "qualificationRoot", "upstreamLicense"],
      {
        id: string(64, SAFE_ID),
        lane: { enum: ["godot-behavior-tree", "dialogue-presentation", "memory-adapter", "animation-fixture"] },
        repository: string(256, GITHUB_REPOSITORY),
        tag: string(64),
        commit: string(40, GIT_SHA),
        gitTreeSha1: string(40, GIT_SHA),
        treeListSha256: string(64, HASH),
        sourceArchiveSha256: string(64, HASH),
        license: { enum: ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC0-1.0"] },
        qualificationRoot: string(256, SAFE_PATH),
        upstreamLicense: closed(["path", "byteLength", "sha256"], { path: string(256, SAFE_PATH), byteLength: { type: "integer", minimum: 1, maximum: 1048576 }, sha256: string(64, HASH) }),
      },
    ),
    executionPolicy: closed(
      ["containerAllowed", "network", "lifecycleScriptsAllowed", "timeoutMs", "outputMaxBytes", "allowedProcessNames"],
      {
        containerAllowed: { const: false },
        network: { enum: ["none", "loopback-only"] },
        lifecycleScriptsAllowed: { const: false },
        timeoutMs: { type: "integer", minimum: 1000, maximum: 120000 },
        outputMaxBytes: { type: "integer", minimum: 1024, maximum: 1048576 },
        allowedProcessNames: { type: "array", maxItems: 8, uniqueItems: true, items: string(64, "^[A-Za-z0-9._-]+$") },
      },
    ),
  },
});

const gate = { enum: ["pass", "fail", "not-proven"] };

export const V2_QUALIFICATION_REPORT_SCHEMA = Object.freeze({
  $schema: JSON_SCHEMA_URI,
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "candidate", "execution", "hardGates", "scores", "evidence", "switchConditions", "diagnosticCodes"],
  properties: {
    format: { const: "matrix-oasis.v2-qualification-report" },
    formatVersion: { const: "0.1.0" },
    canonicalization: { const: "matrix-oasis.canonical-json/1" },
    candidate: closed(["id", "lane", "commit", "gitTreeSha1"], { id: string(64, SAFE_ID), lane: { enum: ["godot-behavior-tree", "dialogue-presentation", "memory-adapter", "animation-fixture"] }, commit: string(40, GIT_SHA), gitTreeSha1: string(40, GIT_SHA) }),
    execution: closed(["status", "attemptCount", "commandCount", "networkObservation", "residualProcessObservation"], { status: { enum: ["executed", "failed", "deferred"] }, attemptCount: { type: "integer", minimum: 0, maximum: 32 }, commandCount: { type: "integer", minimum: 0, maximum: 64 }, networkObservation: gate, residualProcessObservation: gate }),
    hardGates: closed(["license", "reproducibleSource", "secretIsolation", "filesystemIsolation", "authorityCompatibility", "runtimeCompatibility"], { license: gate, reproducibleSource: gate, secretIsolation: gate, filesystemIsolation: gate, authorityCompatibility: gate, runtimeCompatibility: gate }),
    scores: closed(["architectureCompatibility", "standaloneIntegration", "determinismTestability", "securityFailClosed", "maintenanceSourceRisk", "performanceRuntime", "functionality"], { architectureCompatibility: { type: "integer", minimum: 0, maximum: 25 }, standaloneIntegration: { type: "integer", minimum: 0, maximum: 20 }, determinismTestability: { type: "integer", minimum: 0, maximum: 15 }, securityFailClosed: { type: "integer", minimum: 0, maximum: 15 }, maintenanceSourceRisk: { type: "integer", minimum: 0, maximum: 10 }, performanceRuntime: { type: "integer", minimum: 0, maximum: 10 }, functionality: { type: "integer", minimum: 0, maximum: 5 } }),
    evidence: closed(["sourceIdentitySha256", "executionEvidenceSha256", "files"], { sourceIdentitySha256: string(64, HASH), executionEvidenceSha256: string(64, HASH), files: { type: "array", maxItems: 64, items: closed(["name", "byteLength", "sha256"], { name: string(128, "^[A-Za-z0-9._-]+$"), byteLength: { type: "integer", minimum: 0, maximum: 1073741824 }, sha256: string(64, HASH) }) } }),
    switchConditions: { type: "array", minItems: 1, maxItems: 16, items: closed(["code", "observable"], { code: string(64, "^[A-Z][A-Z0-9_]{2,63}$"), observable: string(256) }) },
    diagnosticCodes: { type: "array", maxItems: 64, uniqueItems: true, items: string(96, "^[A-Z][A-Z0-9_]{2,95}$") },
  },
});
