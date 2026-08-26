import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { V2_LANES } from "@matrix-oasis/v2-landscape-contracts";

const LOCK_PATH = "third-party/v2-landscape-references/reference.lock.json";
const R17_LOCK_PATH = "third-party/v2-qualification-references/reference.lock.json";
const HASH = /^[0-9a-f]{64}$/u;
const GIT_SHA = /^[0-9a-f]{40}$/u;
const SAFE_ID = /^[a-z][a-z0-9-]{0,63}$/u;
const SAFE_REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/u;
const ALLOWED_LICENSES = new Set(["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC0-1.0"]);
const CANDIDATE_TYPES = new Set(["open-source", "internal-baseline", "commercial-benchmark", "public-asset"]);
const SOURCE_KINDS = new Set(["git-repository", "git-reference", "github-search-result", "source-archive", "internal-baseline", "public-documentation"]);
const EVIDENCE_STATUSES = new Set(["identity-locked", "reference-only", "identity-gap", "statically-excluded"]);

export class R18SourceError extends Error {
  constructor(code) {
    super(code);
    this.name = "R18SourceError";
    this.code = code;
  }
}

function fail(code) {
  throw new R18SourceError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function exactKeys(value, keys, code) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== [...keys].sort().join(",")) fail(code);
}

function readTracked(moduleRoot, relative, maximum = 4 * 1024 * 1024) {
  if (typeof relative !== "string" || relative.startsWith("/") || relative.includes("\\") || relative.split("/").includes("..")) fail("R18_SOURCE_PATH_INVALID");
  let bytes;
  try {
    bytes = readFileSync(path.join(moduleRoot, ...relative.split("/")));
  } catch {
    fail("R18_SOURCE_FILE_MISSING");
  }
  if (bytes.byteLength > maximum) fail("R18_SOURCE_FILE_TOO_LARGE");
  return bytes;
}

function readJson(moduleRoot, relative, code) {
  try {
    return JSON.parse(readTracked(moduleRoot, relative).toString("utf8"));
  } catch (error) {
    if (error instanceof R18SourceError) throw error;
    fail(code);
  }
}

function validateEvidenceDescriptor(moduleRoot, value, expectedPath, code) {
  exactKeys(value, ["path", "sha256"], code);
  if (value.path !== expectedPath || !HASH.test(value.sha256) || sha256(readTracked(moduleRoot, value.path)) !== value.sha256) fail(code);
}

function r17CandidateIds(r17) {
  return new Set([
    ...r17.executableCandidates.map((item) => item.id),
    ...r17.architectureReferences.map((item) => item.id),
    ...r17.animationFixtures.map((item) => item.id),
    ...r17.deferredAlternatives.map((item) => item.id),
    ...r17.r13ReusedReferences.names.map((name) => name.toLowerCase().replaceAll(/[^a-z0-9]+/gu, "-").replaceAll(/^-|-$/gu, "")),
  ]);
}

function validateDiscoveryEvidence(value) {
  exactKeys(value, ["documents", "github"], "R18_SOURCE_DISCOVERY_INVALID");
  exactKeys(value.documents, ["evidenceSha256", "failureCount", "identityCount", "outputName", "querySetSha256", "reportSha256", "requestCount"], "R18_SOURCE_DOCUMENT_EVIDENCE_INVALID");
  if (
    !/^matrix-oasis-r18-discovery-[a-z0-9-]+$/u.test(value.documents.outputName) ||
    !HASH.test(value.documents.querySetSha256) ||
    !HASH.test(value.documents.reportSha256) ||
    !HASH.test(value.documents.evidenceSha256) ||
    value.documents.identityCount !== 8 ||
    value.documents.failureCount !== 0 ||
    value.documents.requestCount !== 8
  ) fail("R18_SOURCE_DOCUMENT_EVIDENCE_INVALID");
  exactKeys(value.github, [
    "failureCount", "identityCount", "identityEvidenceSha256", "identityOutputName", "identityReportSha256", "identityRequestCount",
    "querySetSha256", "searchEvidenceSha256", "searchOutputName", "searchReportSha256", "searchRequestCount",
  ], "R18_SOURCE_GITHUB_EVIDENCE_INVALID");
  if (
    !/^matrix-oasis-r18-discovery-[a-z0-9-]+$/u.test(value.github.searchOutputName) ||
    !/^matrix-oasis-r18-discovery-[a-z0-9-]+$/u.test(value.github.identityOutputName) ||
    !HASH.test(value.github.querySetSha256) ||
    !HASH.test(value.github.searchReportSha256) ||
    !HASH.test(value.github.searchEvidenceSha256) ||
    !HASH.test(value.github.identityReportSha256) ||
    !HASH.test(value.github.identityEvidenceSha256) ||
    value.github.searchRequestCount !== 8 ||
    !Number.isSafeInteger(value.github.identityRequestCount) ||
    value.github.identityRequestCount < 16 ||
    value.github.identityRequestCount > 23 ||
    !Number.isSafeInteger(value.github.identityCount) ||
    value.github.identityCount < 16 ||
    !Number.isSafeInteger(value.github.failureCount) ||
    value.github.failureCount < 0 ||
    value.github.failureCount > 7 ||
    value.github.identityCount + value.github.failureCount !== value.github.identityRequestCount
  ) fail("R18_SOURCE_GITHUB_EVIDENCE_INVALID");
}

function validateCandidate(moduleRoot, candidate, r17Ids) {
  exactKeys(candidate, [
    "candidateType", "discovery", "id", "laneIds", "license", "maintenance", "name", "newSinceR17", "source", "staticExclusionCode",
  ], "R18_SOURCE_CANDIDATE_INVALID");
  if (
    !SAFE_ID.test(candidate.id) ||
    typeof candidate.name !== "string" ||
    candidate.name.length < 1 ||
    candidate.name.length > 128 ||
    !CANDIDATE_TYPES.has(candidate.candidateType) ||
    candidate.newSinceR17 !== !r17Ids.has(candidate.id) ||
    !Array.isArray(candidate.laneIds) ||
    candidate.laneIds.length < 1 ||
    new Set(candidate.laneIds).size !== candidate.laneIds.length ||
    candidate.laneIds.some((lane) => !V2_LANES.includes(lane)) ||
    (candidate.staticExclusionCode !== null && !/^[A-Z][A-Z0-9_]{2,95}$/u.test(candidate.staticExclusionCode))
  ) fail("R18_SOURCE_CANDIDATE_INVALID");

  exactKeys(candidate.source, ["archiveSha256", "commit", "gitTreeSha1", "host", "identitySha256", "kind", "path"], "R18_SOURCE_IDENTITY_INVALID");
  if (
    !SOURCE_KINDS.has(candidate.source.kind) ||
    typeof candidate.source.host !== "string" ||
    typeof candidate.source.path !== "string" ||
    candidate.source.path.startsWith("/") ||
    candidate.source.path.includes("\\") ||
    candidate.source.path.split("/").includes("..") ||
    !HASH.test(candidate.source.identitySha256)
  ) fail("R18_SOURCE_IDENTITY_INVALID");
  const git = candidate.source.kind === "git-repository";
  if (git !== (candidate.source.host === "github.com" && SAFE_REPOSITORY.test(candidate.source.path) && GIT_SHA.test(candidate.source.commit) && GIT_SHA.test(candidate.source.gitTreeSha1))) fail("R18_SOURCE_IDENTITY_INVALID");
  const gitReference = candidate.source.kind === "git-reference";
  if (gitReference !== (candidate.source.host === "github.com" && SAFE_REPOSITORY.test(candidate.source.path) && GIT_SHA.test(candidate.source.commit) && candidate.source.gitTreeSha1 === null)) fail("R18_SOURCE_IDENTITY_INVALID");
  if (!git && !gitReference && (candidate.source.commit !== null || candidate.source.gitTreeSha1 !== null)) fail("R18_SOURCE_IDENTITY_INVALID");
  if (candidate.source.kind === "source-archive" ? !HASH.test(candidate.source.archiveSha256) : candidate.source.archiveSha256 !== null && !(git && HASH.test(candidate.source.archiveSha256))) fail("R18_SOURCE_IDENTITY_INVALID");
  const searchResult = candidate.source.kind === "github-search-result";
  if (searchResult !== (candidate.source.host === "github.com" && SAFE_REPOSITORY.test(candidate.source.path) && candidate.source.commit === null && candidate.source.gitTreeSha1 === null && candidate.source.archiveSha256 === null)) fail("R18_SOURCE_IDENTITY_INVALID");
  if (candidate.source.kind === "internal-baseline") {
    if (candidate.source.host !== "internal" || !candidate.source.path.startsWith("packages/") || sha256(readTracked(moduleRoot, candidate.source.path)) !== candidate.source.identitySha256) fail("R18_SOURCE_IDENTITY_INVALID");
  }
  if (candidate.candidateType === "commercial-benchmark" && candidate.source.kind !== "public-documentation") fail("R18_SOURCE_IDENTITY_INVALID");

  exactKeys(candidate.license, ["evidenceSha256", "reportedSpdx", "reuseEligible", "status"], "R18_SOURCE_LICENSE_INVALID");
  if (
    typeof candidate.license.reportedSpdx !== "string" ||
    candidate.license.reportedSpdx.length < 1 ||
    candidate.license.reportedSpdx.length > 64 ||
    !["locked", "reported", "unknown", "reference-only"].includes(candidate.license.status) ||
    !HASH.test(candidate.license.evidenceSha256) ||
    candidate.license.reuseEligible !== (candidate.license.status === "locked" && ALLOWED_LICENSES.has(candidate.license.reportedSpdx))
  ) fail("R18_SOURCE_LICENSE_INVALID");

  exactKeys(candidate.maintenance, ["evidenceSha256", "lastActivityYearMonth", "state"], "R18_SOURCE_MAINTENANCE_INVALID");
  if (
    !["active", "maintenance", "archived", "unknown"].includes(candidate.maintenance.state) ||
    (candidate.maintenance.lastActivityYearMonth !== null && !/^\d{4}-(0[1-9]|1[0-2])$/u.test(candidate.maintenance.lastActivityYearMonth)) ||
    !HASH.test(candidate.maintenance.evidenceSha256)
  ) fail("R18_SOURCE_MAINTENANCE_INVALID");

  exactKeys(candidate.discovery, ["evidenceSha256", "layer", "status"], "R18_SOURCE_EVIDENCE_INVALID");
  if (!HASH.test(candidate.discovery.evidenceSha256) || !["r17-lock", "r18-public-search", "internal", "public-documentation"].includes(candidate.discovery.layer) || !EVIDENCE_STATUSES.has(candidate.discovery.status)) fail("R18_SOURCE_EVIDENCE_INVALID");
  if ((candidate.discovery.status === "identity-gap" || candidate.discovery.status === "statically-excluded") !== (candidate.staticExclusionCode !== null)) fail("R18_SOURCE_EVIDENCE_INVALID");
}

export function verifyR18Sources({ moduleRoot }) {
  const lock = readJson(moduleRoot, LOCK_PATH, "R18_SOURCE_LOCK_INVALID");
  exactKeys(lock, [
    "candidates", "discoveryEvidence", "licensePolicy", "profile", "querySets", "runtimeDependency", "schemaVersion", "trackedCandidateSource", "trackedRawEvidence",
  ], "R18_SOURCE_LOCK_INVALID");
  if (
    lock.schemaVersion !== 1 ||
    lock.profile !== "matrix-oasis.v2-landscape-references/1" ||
    lock.runtimeDependency !== false ||
    lock.trackedCandidateSource !== false ||
    lock.trackedRawEvidence !== false
  ) fail("R18_SOURCE_LOCK_INVALID");
  exactKeys(lock.querySets, ["documents", "github", "r17"], "R18_SOURCE_QUERY_SET_INVALID");
  validateEvidenceDescriptor(moduleRoot, lock.querySets.github, "third-party/v2-landscape-references/discovery-query-set.json", "R18_SOURCE_QUERY_SET_INVALID");
  validateEvidenceDescriptor(moduleRoot, lock.querySets.documents, "third-party/v2-landscape-references/discovery-query-set-approved-docs-v1.json", "R18_SOURCE_QUERY_SET_INVALID");
  validateEvidenceDescriptor(moduleRoot, lock.querySets.r17, R17_LOCK_PATH, "R18_SOURCE_QUERY_SET_INVALID");
  validateDiscoveryEvidence(lock.discoveryEvidence);
  if (lock.discoveryEvidence.github.querySetSha256 !== lock.querySets.github.sha256 || lock.discoveryEvidence.documents.querySetSha256 !== lock.querySets.documents.sha256) fail("R18_SOURCE_DISCOVERY_QUERY_MISMATCH");
  exactKeys(lock.licensePolicy, ["allowedSpdx", "commercialReferenceOnly", "unknownFailsClosed"], "R18_SOURCE_LICENSE_POLICY_INVALID");
  if (
    JSON.stringify(lock.licensePolicy.allowedSpdx) !== JSON.stringify([...ALLOWED_LICENSES].sort()) ||
    lock.licensePolicy.commercialReferenceOnly !== true ||
    lock.licensePolicy.unknownFailsClosed !== true
  ) fail("R18_SOURCE_LICENSE_POLICY_INVALID");
  if (!Array.isArray(lock.candidates) || lock.candidates.length < 32 || lock.candidates.length > 256) fail("R18_SOURCE_COVERAGE_INVALID");
  const r17 = readJson(moduleRoot, R17_LOCK_PATH, "R18_SOURCE_R17_LOCK_INVALID");
  const r17Ids = r17CandidateIds(r17);
  const ids = new Set();
  for (const candidate of lock.candidates) {
    validateCandidate(moduleRoot, candidate, r17Ids);
    if (ids.has(candidate.id)) fail("R18_SOURCE_CANDIDATE_DUPLICATE");
    ids.add(candidate.id);
  }
  let entries = 0;
  for (const lane of V2_LANES) {
    const members = lock.candidates.filter((candidate) => candidate.laneIds.includes(lane));
    entries += members.length;
    if (members.length < 6 || members.filter((candidate) => ["open-source", "internal-baseline"].includes(candidate.candidateType)).length < 4 || members.filter((candidate) => candidate.newSinceR17).length < 2) fail("R18_SOURCE_COVERAGE_INVALID");
  }
  if (entries < 48 || lock.candidates.filter((candidate) => candidate.candidateType === "commercial-benchmark" && candidate.laneIds.includes("creator-commercial-benchmark")).length < 4) fail("R18_SOURCE_COVERAGE_INVALID");
  return Object.freeze({
    profile: lock.profile,
    candidates: lock.candidates.length,
    entries,
    githubIdentities: lock.discoveryEvidence.github.identityCount,
    publicDocuments: lock.discoveryEvidence.documents.identityCount,
    lockSha256: sha256(readTracked(moduleRoot, LOCK_PATH)),
  });
}
