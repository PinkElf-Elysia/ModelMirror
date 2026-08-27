import { createHash } from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { V2_LANES } from "@matrix-oasis/v2-landscape-contracts";

const HASH = /^[0-9a-f]{64}$/u;
const ID = /^[a-z][a-z0-9-]{0,95}$/u;
const FIXTURES = Object.freeze({
  "npc-orchestration": "npc-orchestration-ledger",
  "memory-relationships": "memory-ledger-rebuild",
  "dynamic-events": "event-proposal-adjudication",
  "godot-behavior": "godot-behavior-load",
  "dialogue-presentation": "dialogue-restrictive-presentation",
  "character-animation": "character-animation-import",
  "evaluation-observability": "runtime-replay-evidence",
});

export class R18LandscapeHarnessError extends Error {
  constructor(code) {
    super(code);
    this.name = "R18LandscapeHarnessError";
    this.code = code;
  }
}

function fail(code) {
  throw new R18LandscapeHarnessError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(deepFreeze);
  return Object.freeze(value);
}

export const V2_QUALIFICATION_PROFILES = deepFreeze({
  "embedded-godot": {
    network: "none",
    container: "forbidden",
    install: "ignore-scripts-first",
    filesystem: "source-read-only-and-new-tmp-output",
    credentials: "empty",
    timeoutMs: 180000,
    outputMaxBytes: 2097152,
    processNames: ["godot.exe"],
    enforcement: { filesystem: "not-proven-until-execution", network: "not-proven-until-execution", resources: "desktop-time-and-output-only" },
  },
  service: {
    network: "loopback-only",
    container: "separate-candidate-approval-required",
    install: "ignore-scripts-first",
    filesystem: "source-read-only-and-new-tmp-output",
    credentials: "empty",
    timeoutMs: 300000,
    outputMaxBytes: 2097152,
    processNames: ["node.exe", "python.exe"],
    enforcement: { filesystem: "not-proven-until-execution", network: "not-proven-until-execution", resources: "desktop-time-and-output-only" },
  },
  asset: {
    network: "none",
    container: "forbidden",
    install: "not-applicable",
    filesystem: "source-read-only-and-new-tmp-output",
    credentials: "empty",
    timeoutMs: 180000,
    outputMaxBytes: 2097152,
    processNames: ["godot.exe"],
    enforcement: { filesystem: "not-proven-until-execution", network: "not-proven-until-execution", resources: "desktop-time-and-output-only" },
  },
});

function isolationClass(candidate, laneIds) {
  if (candidate.surface.runtimeClass === "commercial" || candidate.candidateType === "commercial-benchmark") fail("R18_COMMERCIAL_EXECUTION_FORBIDDEN");
  if (candidate.surface.runtimeClass === "asset") return "asset";
  if (candidate.surface.runtimeClass === "embedded-godot") return "embedded-godot";
  if (candidate.surface.runtimeClass === "internal") {
    if (laneIds.every((lane) => lane === "character-animation")) return "asset";
    if (laneIds.every((lane) => ["godot-behavior", "dialogue-presentation"].includes(lane))) return "embedded-godot";
  }
  return "service";
}

function source(candidate) {
  return {
    kind: candidate.source.kind,
    host: candidate.source.location.host,
    path: candidate.source.location.path,
    commit: candidate.source.commit,
    gitTreeSha1: candidate.source.gitTreeSha1,
    archiveSha256: candidate.source.archiveSha256,
    identitySha256: candidate.source.identitySha256,
  };
}

function validCandidate(candidate) {
  return candidate && typeof candidate === "object" && ID.test(candidate.id) && Array.isArray(candidate.laneIds) &&
    candidate.source && HASH.test(candidate.source.identitySha256) && candidate.source.location &&
    candidate.license?.qualificationAllowed === true && ["approved", "direct-approved"].includes(candidate.license.closureStatus) &&
    candidate.staticExclusion?.excluded === false;
}

export function createV2QualificationPlan({ candidate, laneIds }) {
  if (!validCandidate(candidate) || !Array.isArray(laneIds) || laneIds.length === 0 || laneIds.some((lane) => !V2_LANES.includes(lane) || !candidate.laneIds.includes(lane) || lane === "creator-commercial-benchmark")) fail("R18_QUALIFICATION_PLAN_INPUT_INVALID");
  const uniqueLanes = V2_LANES.filter((lane) => laneIds.includes(lane));
  const classification = isolationClass(candidate, uniqueLanes);
  const profile = V2_QUALIFICATION_PROFILES[classification];
  const value = {
    format: "matrix-oasis.v2-qualification-plan",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    candidate: {
      id: candidate.id,
      candidateType: candidate.candidateType,
      qualificationClass: candidate.surface.runtimeClass,
      isolationClass: classification,
      laneIds: uniqueLanes,
    },
    source: source(candidate),
    license: {
      spdx: candidate.license.spdx,
      closureStatus: candidate.license.closureStatus,
      evidenceSha256: candidate.license.evidenceSha256,
    },
    execution: profile,
    fixtures: uniqueLanes.map((laneId) => ({ laneId, fixtureId: FIXTURES[laneId] })),
    approval: {
      sourceCheckoutRequired: candidate.source.kind !== "internal-baseline",
      dependencyDownloadApproved: false,
      candidateExecutionApproved: false,
      containerExecutionApproved: false,
    },
    stopConditions: [
      "SOURCE_IDENTITY_DRIFT",
      "LICENSE_CLOSURE_DRIFT",
      "UNDISCLOSED_LIFECYCLE_SCRIPT",
      "UNDISCLOSED_NETWORK_OR_PROCESS",
      "CONTAINER_APPROVAL_REQUIRED",
      "OUTPUT_OR_TIMEOUT_LIMIT",
    ],
  };
  const canonicalJson = canonicalizeJsonValue(value);
  return deepFreeze({ value, canonicalJson, sha256: sha256(Buffer.from(canonicalJson, "utf8")) });
}

export function validateV2QualificationPlan(text) {
  const diagnostics = [];
  let value;
  try {
    value = JSON.parse(text);
    if (canonicalizeJsonValue(value) !== text) diagnostics.push("R18_QUALIFICATION_PLAN_NON_CANONICAL");
  } catch {
    return deepFreeze({ valid: false, diagnostics: ["R18_QUALIFICATION_PLAN_PARSE_FAILED"] });
  }
  const keys = Object.keys(value || {}).sort();
  if (JSON.stringify(keys) !== JSON.stringify(["approval", "candidate", "canonicalization", "execution", "fixtures", "format", "formatVersion", "license", "source", "stopConditions"].sort())) diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  if (value?.format !== "matrix-oasis.v2-qualification-plan" || value?.formatVersion !== "0.1.0" || value?.canonicalization !== "matrix-oasis.canonical-json/1") diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  if (JSON.stringify(Object.keys(value?.candidate || {}).sort()) !== JSON.stringify(["candidateType", "id", "isolationClass", "laneIds", "qualificationClass"].sort()) || !ID.test(value?.candidate?.id || "") || !["open-source", "internal-baseline", "public-asset"].includes(value?.candidate?.candidateType) || !["embedded-godot", "service", "asset", "internal"].includes(value?.candidate?.qualificationClass) || !V2_QUALIFICATION_PROFILES[value?.candidate?.isolationClass] || !Array.isArray(value?.candidate?.laneIds) || value.candidate.laneIds.length === 0 || value.candidate.laneIds.some((lane, index) => !V2_LANES.includes(lane) || lane === "creator-commercial-benchmark" || V2_LANES.indexOf(lane) <= (index === 0 ? -1 : V2_LANES.indexOf(value.candidate.laneIds[index - 1])))) diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  if (JSON.stringify(Object.keys(value?.source || {}).sort()) !== JSON.stringify(["archiveSha256", "commit", "gitTreeSha1", "host", "identitySha256", "kind", "path"].sort()) || !["git-repository", "source-archive", "internal-baseline"].includes(value?.source?.kind) || typeof value?.source?.host !== "string" || typeof value?.source?.path !== "string" || value.source.path.startsWith("/") || value.source.path.includes("..") || value.source.path.includes("\\") || !HASH.test(value?.source?.identitySha256 || "") || (value.source.commit !== null && !/^[0-9a-f]{40}$/u.test(value.source.commit)) || (value.source.gitTreeSha1 !== null && !/^[0-9a-f]{40}$/u.test(value.source.gitTreeSha1)) || (value.source.archiveSha256 !== null && !HASH.test(value.source.archiveSha256))) diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  if (JSON.stringify(Object.keys(value?.license || {}).sort()) !== JSON.stringify(["closureStatus", "evidenceSha256", "spdx"].sort()) || !["approved", "direct-approved"].includes(value?.license?.closureStatus) || typeof value?.license?.spdx !== "string" || !HASH.test(value?.license?.evidenceSha256 || "")) diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  if (canonicalizeJsonValue(value?.execution) !== canonicalizeJsonValue(V2_QUALIFICATION_PROFILES[value?.candidate?.isolationClass] || null)) diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  if (JSON.stringify(Object.keys(value?.approval || {}).sort()) !== JSON.stringify(["candidateExecutionApproved", "containerExecutionApproved", "dependencyDownloadApproved", "sourceCheckoutRequired"].sort()) || typeof value?.approval?.sourceCheckoutRequired !== "boolean") diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  if (value?.approval?.dependencyDownloadApproved !== false || value?.approval?.candidateExecutionApproved !== false || value?.approval?.containerExecutionApproved !== false) diagnostics.push("R18_QUALIFICATION_PLAN_APPROVAL_INVALID");
  if (!Array.isArray(value?.fixtures) || value.fixtures.length !== value?.candidate?.laneIds?.length || value.fixtures.some((fixture, index) => JSON.stringify(Object.keys(fixture || {}).sort()) !== JSON.stringify(["fixtureId", "laneId"].sort()) || fixture.laneId !== value.candidate.laneIds[index] || fixture.fixtureId !== FIXTURES[fixture.laneId])) diagnostics.push("R18_QUALIFICATION_PLAN_FIXTURE_INVALID");
  if (!Array.isArray(value?.stopConditions) || JSON.stringify(value.stopConditions) !== JSON.stringify(["SOURCE_IDENTITY_DRIFT", "LICENSE_CLOSURE_DRIFT", "UNDISCLOSED_LIFECYCLE_SCRIPT", "UNDISCLOSED_NETWORK_OR_PROCESS", "CONTAINER_APPROVAL_REQUIRED", "OUTPUT_OR_TIMEOUT_LIMIT"])) diagnostics.push("R18_QUALIFICATION_PLAN_SCHEMA_INVALID");
  const unique = [...new Set(diagnostics)].sort();
  return deepFreeze(unique.length === 0 ? { valid: true, value, diagnostics: [] } : { valid: false, diagnostics: unique });
}
