import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  V2_CLASS_GATES,
  V2_DESKTOP_GATES,
  V2_LANES,
  evaluateV2CandidateForTier,
  selectV2LaneShortlist,
  validateV2CandidateCatalogJson,
} from "@matrix-oasis/v2-landscape-contracts";

const SOURCE_LOCK_PATH = "third-party/v2-landscape-references/reference.lock.json";
const CATALOG_PATH = "docs/R18_CANDIDATE_CATALOG.json";
const AUDIT_PATH = "third-party/v2-landscape-references/desktop-audit.lock.json";
const EMBEDDED = new Set([
  "beehave", "limboai", "dialogue-manager", "arrow", "godot-behavior-tree", "godot-behavior-tree-plugin",
  "godot-yet-another-behavior-tree", "rakugo-dialogue-system", "sprouty-dialogs", "anima", "godot-aseprite-wizard",
  "dialogic", "godot-ink", "yarn-spinner-godot",
]);
const SERVICE_HEAVY = new Set(["letta", "graphiti", "ai-town", "generative-agents"]);
const LANE_TITLES = Object.freeze({
  "npc-orchestration": "NPC cognition, orchestration and adjudication",
  "memory-relationships": "Persona, memory and relationship state",
  "dynamic-events": "Dynamic tasks, events and emergent narrative",
  "godot-behavior": "Godot-local behavior execution",
  "dialogue-presentation": "Dialogue authoring and runtime presentation",
  "character-animation": "Character form, animation and presentation assets",
  "evaluation-observability": "Evaluation, replay, safety and observability",
  "creator-commercial-benchmark": "Creator one-prompt generation and commercial benchmarks",
});

export class R18LandscapeError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
    this.name = "R18LandscapeError";
  }
}

function fail(code) {
  throw new R18LandscapeError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function readTracked(moduleRoot, relative) {
  try {
    const bytes = readFileSync(path.join(moduleRoot, ...relative.split("/")));
    if (bytes.byteLength > 4 * 1024 * 1024) fail("R18_LANDSCAPE_INPUT_TOO_LARGE");
    return bytes;
  } catch (error) {
    if (error instanceof R18LandscapeError) throw error;
    fail("R18_LANDSCAPE_INPUT_INVALID");
  }
}

function readJson(moduleRoot, relative) {
  try {
    return JSON.parse(readTracked(moduleRoot, relative).toString("utf8"));
  } catch (error) {
    if (error instanceof R18LandscapeError) throw error;
    fail("R18_LANDSCAPE_INPUT_INVALID");
  }
}

function runtimeClass(sourceCandidate) {
  if (sourceCandidate.candidateType === "commercial-benchmark") return "commercial";
  if (sourceCandidate.candidateType === "public-asset" || sourceCandidate.id === "static-character-asset-baseline" || sourceCandidate.id === "quaternius-animated-characters") return "asset";
  if (sourceCandidate.candidateType === "internal-baseline") return "internal";
  if (EMBEDDED.has(sourceCandidate.id) || sourceCandidate.laneIds.every((lane) => ["godot-behavior", "dialogue-presentation", "character-animation"].includes(lane))) return "embedded-godot";
  return "service";
}

function surface(moduleRoot, sourceCandidate, classification) {
  if (sourceCandidate.candidateType === "internal-baseline") {
    const packageJson = readJson(moduleRoot, sourceCandidate.source.path);
    const dependencyCount = new Set([
      ...Object.keys(packageJson.dependencies || {}),
      ...Object.keys(packageJson.peerDependencies || {}),
      ...Object.keys(packageJson.optionalDependencies || {}),
    ]).size;
    return { runtimeClass: classification, evidenceStatus: "complete", requiresContainer: false, lifecycleScripts: 0, nativeBinaries: 0, defaultNetwork: "none", externalServices: 0, dependencyCount };
  }
  if (classification === "commercial") return { runtimeClass: classification, evidenceStatus: "partial", requiresContainer: false, lifecycleScripts: 0, nativeBinaries: 0, defaultNetwork: "external", externalServices: 1, dependencyCount: 0 };
  if (classification === "asset") {
    const complete = sourceCandidate.source.kind === "source-archive";
    return { runtimeClass: classification, evidenceStatus: complete ? "complete" : "unknown", requiresContainer: false, lifecycleScripts: 0, nativeBinaries: 0, defaultNetwork: complete ? "none" : "unknown", externalServices: 0, dependencyCount: 0 };
  }
  const r17 = sourceCandidate.discovery.layer === "r17-lock";
  const embedded = classification === "embedded-godot";
  return {
    runtimeClass: classification,
    evidenceStatus: r17 ? "partial" : "unknown",
    requiresContainer: false,
    lifecycleScripts: 0,
    nativeBinaries: sourceCandidate.id === "limboai" ? 1 : 0,
    defaultNetwork: embedded ? "none" : "external",
    externalServices: embedded ? 0 : SERVICE_HEAVY.has(sourceCandidate.id) ? 2 : 1,
    dependencyCount: sourceCandidate.id === "limboai" ? 1 : 0,
  };
}

function catalogLicense(sourceCandidate) {
  if (sourceCandidate.candidateType === "internal-baseline") {
    return { spdx: "INTERNAL", reuseAllowed: true, qualificationAllowed: true, closureStatus: "approved", evidenceSha256: sourceCandidate.license.evidenceSha256 };
  }
  if (sourceCandidate.candidateType === "commercial-benchmark") {
    return { spdx: "LicenseRef-Public-Docs", reuseAllowed: false, qualificationAllowed: false, closureStatus: "reference-only", evidenceSha256: sourceCandidate.license.evidenceSha256 };
  }
  if (sourceCandidate.license.status === "locked") {
    const completeAsset = sourceCandidate.candidateType === "public-asset" && sourceCandidate.source.kind === "source-archive";
    return {
      spdx: sourceCandidate.license.reportedSpdx,
      reuseAllowed: completeAsset,
      qualificationAllowed: true,
      closureStatus: completeAsset ? "approved" : "direct-approved",
      evidenceSha256: sourceCandidate.license.evidenceSha256,
    };
  }
  return {
    spdx: sourceCandidate.license.reportedSpdx === "COMMERCIAL-REFERENCE" ? "LicenseRef-Public-Docs" : sourceCandidate.license.reportedSpdx,
    reuseAllowed: false,
    qualificationAllowed: false,
    closureStatus: sourceCandidate.license.status === "reference-only" ? "reference-only" : "unknown",
    evidenceSha256: sourceCandidate.license.evidenceSha256,
  };
}

function catalogSource(sourceCandidate) {
  const internalReference = sourceCandidate.source.host === "r17-lock";
  return {
    kind: sourceCandidate.source.kind,
    location: {
      host: internalReference ? "internal" : sourceCandidate.source.host,
      path: internalReference ? `third-party/v2-qualification-references/reference.lock.json/${sourceCandidate.source.path}` : sourceCandidate.source.path.replaceAll("+", "/"),
    },
    commit: sourceCandidate.source.commit,
    gitTreeSha1: sourceCandidate.source.gitTreeSha1,
    archiveSha256: sourceCandidate.source.archiveSha256,
    identitySha256: sourceCandidate.source.identitySha256,
  };
}

function toCatalogCandidate(moduleRoot, sourceCandidate) {
  const classification = runtimeClass(sourceCandidate);
  return {
    id: sourceCandidate.id,
    name: sourceCandidate.name,
    candidateType: sourceCandidate.candidateType,
    newSinceR17: sourceCandidate.newSinceR17,
    laneIds: [...sourceCandidate.laneIds],
    source: catalogSource(sourceCandidate),
    license: catalogLicense(sourceCandidate),
    surface: surface(moduleRoot, sourceCandidate, classification),
    maintenance: {
      state: sourceCandidate.maintenance.state,
      lastReleaseYearMonth: sourceCandidate.maintenance.lastActivityYearMonth,
      evidenceSha256: sourceCandidate.maintenance.evidenceSha256,
    },
    staticExclusion: { excluded: sourceCandidate.staticExclusionCode !== null, code: sourceCandidate.staticExclusionCode },
  };
}

function score(candidate, laneId) {
  const sourceLayer = candidate.source.kind;
  const r17Identity = ["git-repository", "git-reference", "source-archive"].includes(sourceLayer) && !candidate.newSinceR17;
  const evidencePenalty = candidate.surface.evidenceStatus === "unknown" ? 4 : candidate.surface.evidenceStatus === "partial" ? 1 : 0;
  const excluded = candidate.staticExclusion.excluded;
  const authorityBase = { internal: 20, "embedded-godot": 17, asset: 15, service: r17Identity ? 18 : 14, commercial: 10 }[candidate.surface.runtimeClass];
  const standaloneBase = candidate.surface.runtimeClass === "service" ? (r17Identity ? 11 : 8) : { internal: 15, "embedded-godot": 13, asset: 13, commercial: 2 }[candidate.surface.runtimeClass];
  const determinismBase = candidate.surface.runtimeClass === "service" ? (r17Identity ? 7 : 4) : { internal: 10, "embedded-godot": 8, asset: 8, commercial: 2 }[candidate.surface.runtimeClass];
  const securityBase = candidate.surface.runtimeClass === "service" ? (r17Identity ? 7 : 4) : { internal: 10, "embedded-godot": 8, asset: 9, commercial: 2 }[candidate.surface.runtimeClass];
  const performanceBase = { internal: 9, "embedded-godot": 8, asset: 8, service: 5, commercial: 2 }[candidate.surface.runtimeClass];
  const experience = laneId === "character-animation" || laneId === "creator-commercial-benchmark" ? 5 : laneId === "dialogue-presentation" ? 4 : laneId === "dynamic-events" || laneId === "npc-orchestration" ? 3 : 2;
  return {
    authorityCompatibility: Math.max(0, authorityBase - (excluded ? 10 : sourceLayer === "github-search-result" ? 5 : 0)),
    userCommercialValue: excluded ? 3 : candidate.surface.runtimeClass === "commercial" ? 14 : candidate.candidateType === "internal-baseline" ? 10 : r17Identity ? 13 : 11,
    standaloneIntegration: Math.max(0, standaloneBase - evidencePenalty - candidate.surface.nativeBinaries * 2 - (candidate.surface.requiresContainer ? 4 : 0) - Math.min(2, candidate.surface.externalServices)),
    determinismEvaluation: Math.max(0, determinismBase - Math.min(3, evidencePenalty)),
    securityFailClosed: Math.max(0, securityBase - Math.min(3, evidencePenalty) - (candidate.surface.defaultNetwork === "external" ? 1 : 0)),
    licenseMaintenanceSource: candidate.license.closureStatus === "approved" ? 10 : candidate.license.closureStatus === "direct-approved" ? 8 : candidate.license.closureStatus === "reference-only" ? 3 : 2,
    performanceLatencyCost: Math.max(0, performanceBase - candidate.surface.nativeBinaries - (candidate.surface.requiresContainer ? 1 : 0)),
    experienceVisualPotential: experience,
    functionality: excluded ? 1 : candidate.surface.runtimeClass === "commercial" ? 5 : candidate.candidateType === "internal-baseline" ? 3 : 4,
  };
}

function gateEvidence(candidate, qualificationClass) {
  const desktop = new Set(V2_DESKTOP_GATES[qualificationClass]);
  const sourceIdentity = ["git-repository", "source-archive", "internal-baseline"].includes(candidate.source.kind);
  const directLicense = candidate.license.qualificationAllowed;
  const gates = V2_CLASS_GATES[qualificationClass].map((id) => {
    let status = "not-proven";
    if (id === "license") status = directLicense ? "pass" : "not-proven";
    else if (id === "source-identity") status = sourceIdentity ? "pass" : "not-proven";
    else if (qualificationClass === "internal" && id === "authority-boundary") status = "pass";
    else if (qualificationClass === "commercial" && id === "public-evidence") status = "pass";
    if (candidate.staticExclusion.excluded && desktop.has(id)) status = "fail";
    return { id, status, code: status === "fail" ? candidate.staticExclusion.code : null };
  });
  return gates;
}

function qualificationClass(candidate) {
  return candidate.surface.runtimeClass;
}

function evidenceFor(candidate, laneId) {
  const classification = qualificationClass(candidate);
  const scores = score(candidate, laneId);
  return {
    candidateId: candidate.id,
    laneId,
    qualificationClass: classification,
    executionStatus: classification === "commercial" ? "not-required" : "planned",
    harnessAttribution: "not-applicable",
    hardGates: gateEvidence(candidate, classification),
    scores,
    runtimeSurface: { services: candidate.surface.externalServices, nativeBinaries: candidate.surface.nativeBinaries, dependencies: candidate.surface.dependencyCount },
    switchConditions: [{ code: "SWITCH_IF_QUALIFICATION_FAILS", observable: "The locked candidate fails a required isolated qualification gate or its evidence identity drifts." }],
  };
}

function total(scores) {
  return Object.values(scores).reduce((sum, value) => sum + value, 0);
}

export function buildR18DesktopLandscape({ moduleRoot }) {
  const sourceBytes = readTracked(moduleRoot, SOURCE_LOCK_PATH);
  const sourceLock = JSON.parse(sourceBytes.toString("utf8"));
  const candidates = sourceLock.candidates.map((candidate) => toCatalogCandidate(moduleRoot, candidate)).sort((left, right) => left.id.localeCompare(right.id));
  const catalog = {
    format: "matrix-oasis.v2-candidate-catalog",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    catalog: {
      querySetSha256: sourceLock.querySets.github.sha256,
      r17SourceLockSha256: sourceLock.querySets.r17.sha256,
      lanes: V2_LANES.map((id) => ({ id, title: LANE_TITLES[id], executable: id !== "creator-commercial-benchmark", candidateIds: candidates.filter((candidate) => candidate.laneIds.includes(id)).map((candidate) => candidate.id) })),
      candidates,
    },
  };
  const catalogText = canonicalizeJsonValue(catalog);
  const catalogValidation = validateV2CandidateCatalogJson(catalogText);
  if (!catalogValidation.valid) fail("R18_LANDSCAPE_CATALOG_INVALID");
  const evidence = V2_LANES.flatMap((laneId) => candidates.filter((candidate) => candidate.laneIds.includes(laneId)).map((candidate) => evidenceFor(candidate, laneId)));
  const shortlists = selectV2LaneShortlist(catalog, evidence, { maximumPerLane: 3 });
  for (const laneId of V2_LANES.slice(0, -1)) {
    const count = shortlists.find((item) => item.laneId === laneId)?.candidateIds.length ?? 0;
    if (count < 2 || count > 3) fail(`R18_LANDSCAPE_SHORTLIST_QUOTA_INVALID_${laneId.replaceAll("-", "_").toUpperCase()}`);
  }
  const ranks = new Map(shortlists.flatMap((lane) => lane.candidateIds.map((id, index) => [`${lane.laneId}\0${id}`, index + 1])));
  const candidateById = new Map(candidates.map((candidate) => [candidate.id, candidate]));
  const decisions = evidence.map((item) => {
    const candidate = candidateById.get(item.candidateId);
    const evaluated = evaluateV2CandidateForTier(candidate, item);
    const rank = ranks.get(`${item.laneId}\0${item.candidateId}`) ?? null;
    const rejected = evaluated.conclusion === "rejected";
    return {
      candidateId: item.candidateId,
      laneId: item.laneId,
      qualificationClass: item.qualificationClass,
      executionStatus: item.executionStatus,
      harnessAttribution: item.harnessAttribution,
      tier: evaluated.tier,
      conclusion: evaluated.conclusion,
      confidence: rank !== null || item.qualificationClass === "commercial" ? "medium" : "low",
      hardGates: item.hardGates,
      scores: item.scores,
      total: total(item.scores),
      runtimeSurface: item.runtimeSurface,
      shortlistRank: rank,
      evidenceSha256: [candidate.source.identitySha256, candidate.license.evidenceSha256].filter((value, index, values) => values.indexOf(value) === index).sort(),
      switchConditions: item.switchConditions,
      exclusionCode: rejected ? candidate.staticExclusion.code || "R18_DESKTOP_SCORE_BELOW_THRESHOLD" : null,
    };
  });
  const audit = {
    format: "matrix-oasis.v2-desktop-audit",
    formatVersion: "0.1.0",
    catalogSha256: sha256(Buffer.from(catalogText, "utf8")),
    sourceLockSha256: sha256(sourceBytes),
    policy: { shortlistMinimumScore: 70, integrationMinimumScore: 80, nearTieScoreDelta: 5, productionRecommendationAllowed: false },
    shortlists,
    decisions,
  };
  return Object.freeze({ catalogText, auditText: canonicalizeJsonValue(audit), catalog, audit });
}

export function verifyR18DesktopLandscape({ moduleRoot }) {
  const built = buildR18DesktopLandscape({ moduleRoot });
  const trackedCatalog = readTracked(moduleRoot, CATALOG_PATH).toString("utf8");
  const trackedAudit = readTracked(moduleRoot, AUDIT_PATH).toString("utf8");
  if (trackedCatalog !== built.catalogText || trackedAudit !== built.auditText) fail("R18_LANDSCAPE_TRACKED_OUTPUT_DRIFT");
  return Object.freeze({
    candidates: built.catalog.catalog.candidates.length,
    entries: built.audit.decisions.length,
    shortlists: built.audit.shortlists.filter((item) => item.candidateIds.length > 0).length,
    catalogSha256: built.audit.catalogSha256,
    auditSha256: sha256(Buffer.from(built.auditText, "utf8")),
  });
}

export const R18_LANDSCAPE_OUTPUTS = Object.freeze({ catalog: CATALOG_PATH, audit: AUDIT_PATH });
