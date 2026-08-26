import { createHash } from "node:crypto";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const HASH = /^[0-9a-f]{64}$/u;
const GIT_SHA = /^[0-9a-f]{40}$/u;
const ALLOWED = new Set(["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC0-1.0"]);

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function canonical(value) {
  return canonicalizeJsonValue(value);
}

function evidenceHash(value) {
  return sha256(Buffer.from(canonical(value), "utf8"));
}

function readJsonFile(filePath, code) {
  try {
    const bytes = readFileSync(filePath);
    if (bytes.byteLength > 4 * 1024 * 1024) fail(code);
    return { bytes, value: JSON.parse(bytes.toString("utf8")) };
  } catch (error) {
    if (error?.code === code) throw error;
    fail(code);
  }
}

function directTemporaryDirectory(directory) {
  try {
    const resolved = path.resolve(directory);
    const root = path.dirname(resolved);
    if (
      path.basename(root).toLowerCase() !== "tmp" ||
      path.dirname(root) !== path.parse(root).root ||
      path.parse(root).root.slice(0, 1).toLowerCase() !== "c" ||
      realpathSync.native(resolved) !== resolved ||
      lstatSync(resolved).isSymbolicLink() ||
      !lstatSync(resolved).isDirectory()
    ) fail("R18_SOURCE_BUILD_EVIDENCE_INVALID");
    return resolved;
  } catch (error) {
    if (error?.code === "R18_SOURCE_BUILD_EVIDENCE_INVALID") throw error;
    fail("R18_SOURCE_BUILD_EVIDENCE_INVALID");
  }
}

function readEvidence(directory, name) {
  return readJsonFile(path.join(directTemporaryDirectory(directory), name), "R18_SOURCE_BUILD_EVIDENCE_INVALID");
}

function repositoryPath(repositoryUrl) {
  const parsed = new URL(repositoryUrl);
  if (parsed.protocol !== "https:" || parsed.hostname !== "github.com") fail("R18_SOURCE_BUILD_R17_INVALID");
  return parsed.pathname.replace(/^\//u, "").replace(/\.git$/u, "");
}

function source({ kind, host, sourcePath, commit = null, tree = null, archive = null, identity }) {
  return { kind, host, path: sourcePath, commit, gitTreeSha1: tree, archiveSha256: archive, identitySha256: identity };
}

function license(status, reportedSpdx, evidenceSha256) {
  return { status, reportedSpdx, evidenceSha256, reuseEligible: status === "locked" && ALLOWED.has(reportedSpdx) };
}

function maintenance(state, lastActivityYearMonth, evidenceSha256) {
  return { state, lastActivityYearMonth, evidenceSha256 };
}

function discovery(layer, status, evidenceSha256) {
  return { layer, status, evidenceSha256 };
}

function baseCandidate(seed, candidateType, candidateSource, candidateLicense, candidateMaintenance, candidateDiscovery, staticExclusionCode, newSinceR17) {
  return {
    id: seed.id,
    name: seed.name,
    candidateType,
    laneIds: [...seed.laneIds],
    source: candidateSource,
    license: candidateLicense,
    maintenance: candidateMaintenance,
    discovery: candidateDiscovery,
    staticExclusionCode,
    newSinceR17,
  };
}

function findR17Candidate(r17, id) {
  return r17.executableCandidates.find((item) => item.id === id) ||
    r17.architectureReferences.find((item) => item.id === id) ||
    r17.animationFixtures.find((item) => item.id === id) ||
    r17.deferredAlternatives.find((item) => item.id === id) || null;
}

function buildR17Candidate(seed, r17, r13, r17Hash) {
  const item = findR17Candidate(r17, seed.id);
  if (item?.repository && item.commit && item.gitTreeSha1) {
    const archive = item.sourceArchive?.sha256 || item.archive?.sha256 || null;
    const identity = evidenceHash({ repository: item.repository, commit: item.commit, gitTreeSha1: item.gitTreeSha1, archiveSha256: archive });
    const licenseHash = item.upstreamLicense?.sha256 || item.noteSha256;
    return baseCandidate(seed, "open-source", source({ kind: "git-repository", host: "github.com", sourcePath: repositoryPath(item.repository), commit: item.commit, tree: item.gitTreeSha1, archive, identity }), license("locked", item.license, licenseHash), maintenance("unknown", null, identity), discovery("r17-lock", "identity-locked", r17Hash), null, false);
  }
  if (item?.archive?.sha256 && item.sourcePage) {
    return baseCandidate(seed, "public-asset", source({ kind: "source-archive", host: "kenney.nl", sourcePath: "assets/animated-characters-retro", archive: item.archive.sha256, identity: item.archive.sha256 }), license("locked", item.license, item.upstreamLicense.sha256), maintenance("unknown", null, item.noteSha256), discovery("r17-lock", "identity-locked", r17Hash), null, false);
  }
  if (item && seed.id === "kaykit-animated-character") {
    const identity = evidenceHash(item);
    return baseCandidate(seed, "public-asset", source({ kind: "public-documentation", host: "r17-lock", sourcePath: seed.id, identity }), license("unknown", "NOASSERTION", identity), maintenance("unknown", null, identity), discovery("r17-lock", "identity-gap", r17Hash), "R17_FIXED_ARCHIVE_HASH_REQUIRED", false);
  }
  const referenceName = seed.id === "godogen" ? "Godogen" : seed.id === "gamecraft-bench" ? "GameCraft-Bench" : null;
  const reference = r13.references.find((entry) => entry.name === referenceName);
  if (!reference || !GIT_SHA.test(reference.commit)) fail("R18_SOURCE_BUILD_R17_INVALID");
  const identity = evidenceHash({ repository: reference.repository, commit: reference.commit });
  return baseCandidate(seed, "open-source", source({ kind: "git-reference", host: "github.com", sourcePath: repositoryPath(reference.repository), commit: reference.commit, identity }), license("locked", reference.license, reference.upstreamLicense.sha256), maintenance("unknown", null, identity), discovery("r17-lock", "reference-only", r17Hash), null, false);
}

function flattenSearch(search) {
  const map = new Map();
  for (const lane of search.lanes || []) {
    for (const repository of lane.repositories || []) {
      const key = repository.repository.toLowerCase();
      if (!map.has(key) || repository.rank < map.get(key).rank) map.set(key, repository);
    }
  }
  return map;
}

function buildSearchCandidate(seed, searchMap, identityMap, searchHash) {
  const found = searchMap.get(seed.repository.toLowerCase());
  if (!found) fail("R18_SOURCE_BUILD_SEARCH_CANDIDATE_MISSING");
  const identityEntry = identityMap.get(seed.repository.toLowerCase());
  const identity = identityEntry ? evidenceHash({ repository: seed.repository, commit: identityEntry.commit, gitTreeSha1: identityEntry.gitTreeSha1 }) : evidenceHash(found);
  const sourceValue = identityEntry
    ? source({ kind: "git-repository", host: "github.com", sourcePath: seed.repository, commit: identityEntry.commit, tree: identityEntry.gitTreeSha1, identity })
    : source({ kind: "github-search-result", host: "github.com", sourcePath: seed.repository, identity });
  const exclusion = seed.staticExclusionCode || (identityEntry ? null : "R18_IDENTITY_NOT_LOCKED");
  const status = exclusion ? (identityEntry ? "statically-excluded" : "identity-gap") : "identity-locked";
  const activity = found.archived ? "archived" : found.pushedAt.slice(0, 7) >= "2025-01" ? "active" : "maintenance";
  const reported = found.licenseSpdx || "NOASSERTION";
  return baseCandidate(seed, "open-source", sourceValue, license("reported", reported, evidenceHash({ repository: seed.repository, reportedSpdx: reported })), maintenance(activity, found.pushedAt.slice(0, 7), evidenceHash(found)), discovery("r18-public-search", status, searchHash), exclusion, true);
}

function buildInternalCandidate(moduleRoot, seed) {
  const packagePath = `${seed.path}/package.json`;
  const bytes = readFileSync(path.join(moduleRoot, ...packagePath.split("/")));
  const identity = sha256(bytes);
  return baseCandidate(seed, "internal-baseline", source({ kind: "internal-baseline", host: "internal", sourcePath: packagePath, identity }), license("reference-only", "INTERNAL", identity), maintenance("active", "2026-08", identity), discovery("internal", "identity-locked", identity), null, true);
}

function buildCommercialCandidate(seed, documentMap, documentHash) {
  const documents = seed.documentIds.map((id) => {
    const document = documentMap.get(id);
    if (!document) fail("R18_SOURCE_BUILD_DOCUMENT_MISSING");
    return document;
  });
  const hosts = new Set(documents.map((item) => item.source.host));
  if (hosts.size !== 1) fail("R18_SOURCE_BUILD_DOCUMENT_INVALID");
  const identity = evidenceHash(documents);
  return baseCandidate(seed, "commercial-benchmark", source({ kind: "public-documentation", host: documents[0].source.host, sourcePath: seed.documentIds.join("+"), identity }), license("reference-only", "COMMERCIAL-REFERENCE", identity), maintenance("active", "2026-08", identity), discovery("public-documentation", "reference-only", documentHash), null, true);
}

function reportRequestCount(report, host) {
  return report.requestCounts?.find((item) => item.host === host)?.count ?? 0;
}

export function buildR18SourceLock({ moduleRoot, searchDirectory, identityDirectory, documentsDirectory }) {
  const seed = readJsonFile(path.join(moduleRoot, "third-party", "v2-landscape-references", "candidate-seed-manifest.json"), "R18_SOURCE_BUILD_SEED_INVALID").value;
  const r17Descriptor = readJsonFile(path.join(moduleRoot, "third-party", "v2-qualification-references", "reference.lock.json"), "R18_SOURCE_BUILD_R17_INVALID");
  const r13 = readJsonFile(path.join(moduleRoot, "third-party", "spatial-layout-references", "reference.lock.json"), "R18_SOURCE_BUILD_R13_INVALID").value;
  const currentQuery = readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "discovery-query-set.json"));
  const documentQuery = readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "discovery-query-set-approved-docs-v1.json"));
  const search = readEvidence(searchDirectory, "public-search-evidence.json");
  const searchReport = readEvidence(searchDirectory, "discovery-report.json");
  const identities = readEvidence(identityDirectory, "repository-identity-evidence.json");
  const identityReport = readEvidence(identityDirectory, "discovery-report.json");
  const documents = readEvidence(documentsDirectory, "public-document-evidence.json");
  const documentReport = readEvidence(documentsDirectory, "discovery-report.json");
  const queryHash = sha256(currentQuery);
  const documentQueryHash = sha256(documentQuery);
  if (
    search.value.querySetSha256 !== queryHash || identityReport.value.querySetSha256 !== queryHash ||
    searchReport.value.querySetSha256 !== queryHash || documentReport.value.querySetSha256 !== documentQueryHash ||
    identities.value.format !== "matrix-oasis.r18-repository-identity-evidence" ||
    documents.value.format !== "matrix-oasis.r18-public-document-evidence"
  ) fail("R18_SOURCE_BUILD_EVIDENCE_MISMATCH");
  const searchMap = flattenSearch(search.value);
  const identityMap = new Map((identities.value.repositories || []).map((item) => [item.repository.toLowerCase(), item]));
  const documentMap = new Map((documents.value.documents || []).map((item) => [item.id, item]));
  const r17Hash = sha256(r17Descriptor.bytes);
  const candidates = [
    ...seed.r17Candidates.map((item) => buildR17Candidate(item, r17Descriptor.value, r13, r17Hash)),
    ...seed.searchCandidates.map((item) => buildSearchCandidate(item, searchMap, identityMap, sha256(search.bytes))),
    ...seed.internalCandidates.map((item) => buildInternalCandidate(moduleRoot, item)),
    ...seed.commercialCandidates.map((item) => buildCommercialCandidate(item, documentMap, sha256(documents.bytes))),
  ].sort((left, right) => left.id.localeCompare(right.id));
  if (new Set(candidates.map((item) => item.id)).size !== candidates.length) fail("R18_SOURCE_BUILD_DUPLICATE");
  const identityFailures = identities.value.failures || [];
  return {
    schemaVersion: 1,
    profile: "matrix-oasis.v2-landscape-references/1",
    runtimeDependency: false,
    trackedCandidateSource: false,
    trackedRawEvidence: false,
    querySets: {
      github: { path: "third-party/v2-landscape-references/discovery-query-set.json", sha256: queryHash },
      documents: { path: "third-party/v2-landscape-references/discovery-query-set-approved-docs-v1.json", sha256: documentQueryHash },
      r17: { path: "third-party/v2-qualification-references/reference.lock.json", sha256: r17Hash },
    },
    discoveryEvidence: {
      documents: {
        outputName: path.basename(directTemporaryDirectory(documentsDirectory)),
        querySetSha256: documentQueryHash,
        reportSha256: sha256(documentReport.bytes),
        evidenceSha256: sha256(documents.bytes),
        requestCount: Object.values(documentReport.value.requestCounts || []).length === 0 ? 0 : documentReport.value.requestCounts.reduce((sum, item) => sum + item.count, 0),
        identityCount: documents.value.documents.length,
        failureCount: 0,
      },
      github: {
        searchOutputName: path.basename(directTemporaryDirectory(searchDirectory)),
        searchReportSha256: sha256(searchReport.bytes),
        searchEvidenceSha256: sha256(search.bytes),
        searchRequestCount: reportRequestCount(searchReport.value, "api.github.com"),
        identityOutputName: path.basename(directTemporaryDirectory(identityDirectory)),
        identityReportSha256: sha256(identityReport.bytes),
        identityEvidenceSha256: sha256(identities.bytes),
        identityRequestCount: reportRequestCount(identityReport.value, "api.github.com"),
        identityCount: identities.value.repositories.length,
        failureCount: identityFailures.length,
        querySetSha256: queryHash,
      },
    },
    licensePolicy: { allowedSpdx: [...ALLOWED].sort(), commercialReferenceOnly: true, unknownFailsClosed: true },
    candidates,
  };
}

export function canonicalR18SourceLock(value) {
  return canonical(value);
}
