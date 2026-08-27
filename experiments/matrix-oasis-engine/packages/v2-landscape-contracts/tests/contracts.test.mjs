import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  V2_CANDIDATE_CATALOG_SCHEMA,
  V2_DECISION_LANDSCAPE_SCHEMA,
  V2_LANES,
  V2_ROADMAP_SCHEMA,
  evaluateV2CandidateForTier,
  selectV2LaneShortlist,
  validateV2CandidateCatalogJson,
  validateV2DecisionLandscapeJson,
  validateV2RoadmapJson,
} from "../src/index.mjs";

const HASH = "a".repeat(64);

function candidate(index, laneIds, candidateType = "open-source") {
  const git = candidateType === "open-source";
  const commercial = candidateType === "commercial-benchmark";
  return {
    id: `candidate-${String(index).padStart(2, "0")}`,
    name: `Candidate ${index}`,
    candidateType,
    newSinceR17: index % 3 !== 0,
    laneIds,
    source: {
      kind: commercial ? "public-documentation" : git ? "git-repository" : "internal-baseline",
      location: {
        host: git ? "github.com" : commercial ? "example.com" : "internal",
        path: `candidate-${index}`,
      },
      commit: git ? String(index % 10).repeat(40) : null,
      gitTreeSha1: git ? String((index + 1) % 10).repeat(40) : null,
      archiveSha256: null,
      identitySha256: String(index % 10).repeat(64),
    },
    license: {
      spdx: commercial ? "LicenseRef-Public-Docs" : "MIT",
      reuseAllowed: !commercial,
      qualificationAllowed: !commercial,
      closureStatus: commercial ? "reference-only" : "approved",
      evidenceSha256: String((index + 2) % 10).repeat(64),
    },
    surface: {
      runtimeClass: commercial ? "commercial" : git ? "service" : "internal",
      evidenceStatus: "complete",
      requiresContainer: false,
      lifecycleScripts: 0,
      nativeBinaries: 0,
      defaultNetwork: commercial ? "external" : "none",
      externalServices: commercial ? 1 : 0,
      dependencyCount: index,
    },
    maintenance: { state: "active", lastReleaseYearMonth: "2026-08", evidenceSha256: String((index + 3) % 10).repeat(64) },
    staticExclusion: { excluded: false, code: null },
  };
}

function catalog() {
  const candidates = Array.from({ length: 32 }, (_, index) => candidate(index, []));
  const lanes = V2_LANES.map((id, laneIndex) => {
    const indexes = Array.from({ length: 6 }, (_, offset) => (laneIndex * 4 + offset) % 32);
    if (laneIndex === 7) {
      indexes.splice(0, indexes.length, 28, 29, 30, 31, 0, 1, 2, 3);
      for (const index of [28, 29, 30, 31]) candidates[index] = candidate(index, [], "commercial-benchmark");
    }
    return { id, title: `Lane ${laneIndex}`, executable: laneIndex !== 7, candidateIds: indexes.map((index) => candidates[index].id) };
  });
  for (const item of candidates) item.laneIds = lanes.filter((lane) => lane.candidateIds.includes(item.id)).map((lane) => lane.id);
  return {
    format: "matrix-oasis.v2-candidate-catalog",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    catalog: { querySetSha256: HASH, r17SourceLockSha256: "b".repeat(64), lanes, candidates },
  };
}

function scores(offset = 0) {
  return {
    authorityCompatibility: 17 + offset,
    userCommercialValue: 12,
    standaloneIntegration: 12,
    determinismEvaluation: 8,
    securityFailClosed: 8,
    licenseMaintenanceSource: 8,
    performanceLatencyCost: 8,
    experienceVisualPotential: 4,
    functionality: 4,
  };
}

function gates(qualificationClass, status = "pass") {
  const byClass = {
    "embedded-godot": ["license", "source-identity", "authority-boundary", "execution-isolation", "runtime-compatibility", "determinism"],
    service: ["license", "source-identity", "authority-boundary", "execution-isolation", "ledger-rebuild", "fail-closed"],
    asset: ["license", "source-identity", "import-identity", "runtime-compatibility", "performance"],
    commercial: ["public-evidence"],
    internal: ["authority-boundary", "determinism", "fail-closed", "runtime-compatibility"],
  };
  return byClass[qualificationClass].map((id) => ({ id, status, code: status === "fail" ? "HARD_GATE_FAILED" : null }));
}

function evidence(candidateId, laneId, qualificationClass = "service", overrides = {}) {
  return {
    candidateId,
    laneId,
    qualificationClass,
    executionStatus: "executed",
    harnessAttribution: "candidate",
    hardGates: gates(qualificationClass),
    scores: scores(),
    runtimeSurface: { services: 0, nativeBinaries: 0, dependencies: 4 },
    switchConditions: [{ code: "SWITCH_IF_TRACE_DRIFTS", observable: "The locked fixture produces a different trace." }],
    ...overrides,
  };
}

function landscape() {
  const source = catalog();
  const decisions = [];
  for (const [laneIndex, lane] of source.catalog.lanes.entries()) {
    lane.candidateIds.forEach((candidateId, index) => {
      const commercial = laneIndex === 7;
      const shortlisted = !commercial && index < 2;
      const executed = Number(candidateId.slice(-2)) < 12;
      decisions.push({
        candidateId,
        laneId: lane.id,
        qualificationClass: commercial ? "commercial" : "service",
        executionStatus: executed ? "executed" : "planned",
        harnessAttribution: commercial ? "not-applicable" : "candidate",
        tier: shortlisted ? "executable-shortlist" : "architecture-reference",
        conclusion: shortlisted ? "backup" : "deferred",
        confidence: shortlisted ? "medium" : "low",
        hardGates: gates(commercial ? "commercial" : "service", executed || commercial ? "pass" : "not-proven"),
        scores: scores(),
        total: 81,
        runtimeSurface: { services: commercial ? 1 : 0, nativeBinaries: 0, dependencies: index },
        shortlistRank: shortlisted ? index + 1 : null,
        evidenceSha256: [String((decisions.length + 1) % 10).repeat(64)],
        switchConditions: [{ code: "SWITCH_IF_EVIDENCE_DRIFTS", observable: "A locked evidence hash changes." }],
        exclusionCode: null,
      });
    });
  }
  return {
    format: "matrix-oasis.v2-decision-landscape",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    catalogSha256: HASH,
    policy: { shortlistMinimumScore: 70, integrationMinimumScore: 80, nearTieScoreDelta: 5, minimumExecutedCandidates: 12, maximumExecutedCandidates: 16 },
    decisions,
  };
}

function roadmap() {
  const ids = ["R19", "R20", "R21", "R22", "R23", "R24", "R25"];
  return {
    format: "matrix-oasis.v2-roadmap",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    decisionLandscapeSha256: HASH,
    rounds: ids.map((id, index) => ({
      id,
      objective: `Bounded objective ${id}`,
      dependsOn: [index === 0 ? "R18" : ids[index - 1]],
      entryGates: ["PREVIOUS_ROUND_QUALIFIED"],
      exitGates: ["CURRENT_ROUND_QUALIFIED"],
      prohibited: ["CROSS_ROUND_IMPLEMENTATION"],
      rollback: `Revert ${id} commits without altering earlier qualified rounds.`,
    })),
  };
}

test("three schemas and the lane vocabulary are deeply frozen", () => {
  assert.equal(Object.isFrozen(V2_CANDIDATE_CATALOG_SCHEMA.properties), true);
  assert.equal(Object.isFrozen(V2_DECISION_LANDSCAPE_SCHEMA.properties.policy), true);
  assert.equal(Object.isFrozen(V2_ROADMAP_SCHEMA.properties.rounds), true);
  assert.equal(Object.isFrozen(V2_LANES), true);
  assert.equal(V2_LANES.length, 8);
});

test("canonical catalog enforces the eight-lane coverage quotas", () => {
  const value = catalog();
  const result = validateV2CandidateCatalogJson(canonicalizeJsonValue(value));
  assert.equal(result.valid, true);
  assert.equal(Object.isFrozen(result.value.catalog.candidates[0]), true);

  const insufficient = catalog();
  insufficient.catalog.lanes[0].candidateIds = insufficient.catalog.lanes[0].candidateIds.slice(0, 5);
  assert.equal(validateV2CandidateCatalogJson(canonicalizeJsonValue(insufficient)).valid, false);

  const oneSided = catalog();
  oneSided.catalog.candidates[0].laneIds = oneSided.catalog.candidates[0].laneIds.slice(1);
  assert.ok(validateV2CandidateCatalogJson(canonicalizeJsonValue(oneSided)).diagnostics.some((item) => item.code === "V2_CANDIDATE_CATALOG_LANE_MEMBER_INVALID"));
});

test("catalog rejects unknown fields, duplicate IDs, license drift and commercial reuse", () => {
  const unknown = catalog();
  unknown.catalog.candidates[0].undisclosedField = "must-not-echo";
  const unknownResult = validateV2CandidateCatalogJson(canonicalizeJsonValue(unknown));
  assert.ok(unknownResult.diagnostics.some((item) => item.code === "V2_CANDIDATE_CATALOG_SCHEMA_UNKNOWN_PROPERTY"));
  assert.doesNotMatch(JSON.stringify(unknownResult), /must-not-echo|undisclosedField/);

  const duplicate = catalog();
  duplicate.catalog.candidates[1].id = duplicate.catalog.candidates[0].id;
  assert.ok(validateV2CandidateCatalogJson(canonicalizeJsonValue(duplicate)).diagnostics.some((item) => item.code === "V2_CANDIDATE_CATALOG_CANDIDATE_DUPLICATE"));

  const license = catalog();
  license.catalog.candidates[0].license.reuseAllowed = false;
  assert.ok(validateV2CandidateCatalogJson(canonicalizeJsonValue(license)).diagnostics.some((item) => item.code === "V2_CANDIDATE_CATALOG_LICENSE_INCONSISTENT"));

  const traversal = catalog();
  traversal.catalog.candidates[0].source.location.path = "../outside";
  assert.equal(validateV2CandidateCatalogJson(canonicalizeJsonValue(traversal)).valid, false);
});

test("landscape accepts layered decisions and rejects score, rank, commercial and evidence-gap lies", () => {
  assert.equal(validateV2DecisionLandscapeJson(canonicalizeJsonValue(landscape())).valid, true);

  const score = landscape();
  score.decisions[0].total = 100;
  assert.ok(validateV2DecisionLandscapeJson(canonicalizeJsonValue(score)).diagnostics.some((item) => item.code === "V2_DECISION_LANDSCAPE_TOTAL_MISMATCH"));

  const commercial = landscape();
  commercial.decisions.at(-1).tier = "executable-shortlist";
  commercial.decisions.at(-1).shortlistRank = 3;
  assert.ok(validateV2DecisionLandscapeJson(canonicalizeJsonValue(commercial)).diagnostics.some((item) => item.code === "V2_DECISION_LANDSCAPE_COMMERCIAL_TIER_INVALID"));

  const unresolved = landscape();
  Object.assign(unresolved.decisions[0], { executionStatus: "failed", harnessAttribution: "unresolved", conclusion: "rejected", exclusionCode: "HARNESS_FAILED" });
  assert.ok(validateV2DecisionLandscapeJson(canonicalizeJsonValue(unresolved)).diagnostics.some((item) => item.code === "V2_DECISION_LANDSCAPE_EVIDENCE_GAP_MISCLASSIFIED"));

  const missingGate = landscape();
  missingGate.decisions[0].hardGates.pop();
  assert.ok(validateV2DecisionLandscapeJson(canonicalizeJsonValue(missingGate)).diagnostics.some((item) => item.code === "V2_DECISION_LANDSCAPE_GATE_SET_INVALID"));

  const repeatedExecution = landscape();
  for (const decision of repeatedExecution.decisions.filter((item) => item.candidateId === "candidate-11")) decision.executionStatus = "planned";
  assert.ok(validateV2DecisionLandscapeJson(canonicalizeJsonValue(repeatedExecution)).diagnostics.some((item) => item.code === "V2_DECISION_LANDSCAPE_EXECUTION_QUOTA"));

  const rankGap = landscape();
  for (const decision of rankGap.decisions.filter((item) => item.laneId === V2_LANES[0] && item.shortlistRank !== null)) decision.shortlistRank += 1;
  assert.ok(validateV2DecisionLandscapeJson(canonicalizeJsonValue(rankGap)).diagnostics.some((item) => item.code === "V2_DECISION_LANDSCAPE_SHORTLIST_RANK_GAP"));
});

test("roadmap locks R19 through R25 order and forward dependencies fail closed", () => {
  assert.equal(validateV2RoadmapJson(canonicalizeJsonValue(roadmap())).valid, true);
  const forward = roadmap();
  forward.rounds[1].dependsOn = ["R24"];
  assert.ok(validateV2RoadmapJson(canonicalizeJsonValue(forward)).diagnostics.some((item) => item.code === "V2_ROADMAP_DEPENDENCY_ORDER_INVALID"));
});

test("high scores cannot override gates and unresolved Harness failures stay evidence gaps", () => {
  const item = catalog().catalog.candidates[0];
  const highFailure = evidence(item.id, V2_LANES[0], "service", { hardGates: gates("service", "fail") });
  assert.deepEqual(
    { tier: evaluateV2CandidateForTier(item, highFailure).tier, conclusion: evaluateV2CandidateForTier(item, highFailure).conclusion },
    { tier: "architecture-reference", conclusion: "rejected" },
  );

  const unresolved = evidence(item.id, V2_LANES[0], "service", { executionStatus: "failed", harnessAttribution: "unresolved" });
  assert.deepEqual(
    { conclusion: evaluateV2CandidateForTier(item, unresolved).conclusion, evidenceGap: evaluateV2CandidateForTier(item, unresolved).evidenceGap },
    { conclusion: "deferred", evidenceGap: true },
  );

  const candidateFailure = evidence(item.id, V2_LANES[0], "service", { executionStatus: "failed", harnessAttribution: "candidate" });
  assert.equal(evaluateV2CandidateForTier(item, candidateFailure).conclusion, "rejected");

  const incomplete = evidence(item.id, V2_LANES[0]);
  incomplete.hardGates.pop();
  assert.deepEqual(
    { candidateId: evaluateV2CandidateForTier(item, incomplete).candidateId, conclusion: evaluateV2CandidateForTier(item, incomplete).conclusion },
    { candidateId: "invalid", conclusion: "deferred" },
  );

  const invalidScore = evidence(item.id, V2_LANES[0]);
  invalidScore.scores.authorityCompatibility = 100;
  assert.equal(evaluateV2CandidateForTier(item, invalidScore).candidateId, "invalid");
  assert.equal(evaluateV2CandidateForTier(item, evidence(item.id, V2_LANES[0]), { shortlistMinimumScore: -1 }).candidateId, "invalid");
});

test("commercial products remain references while qualified reusable candidates can be recommended", () => {
  const source = catalog();
  const commercial = source.catalog.candidates.find((item) => item.candidateType === "commercial-benchmark");
  const benchmark = evaluateV2CandidateForTier(commercial, evidence(commercial.id, V2_LANES[7], "commercial"));
  assert.deepEqual({ tier: benchmark.tier, conclusion: benchmark.conclusion }, { tier: "architecture-reference", conclusion: "backup" });

  const reusable = source.catalog.candidates[0];
  const qualified = evaluateV2CandidateForTier(reusable, evidence(reusable.id, V2_LANES[0]));
  assert.deepEqual({ tier: qualified.tier, conclusion: qualified.conclusion, total: qualified.total }, { tier: "integration-recommended", conclusion: "recommended", total: 81 });

  const directOnly = structuredClone(reusable);
  Object.assign(directOnly.license, { closureStatus: "direct-approved", qualificationAllowed: true, reuseAllowed: false });
  const planned = evidence(directOnly.id, V2_LANES[0], "service", { executionStatus: "planned" });
  const shortlisted = evaluateV2CandidateForTier(directOnly, planned);
  assert.deepEqual({ tier: shortlisted.tier, conclusion: shortlisted.conclusion }, { tier: "executable-shortlist", conclusion: "backup" });
  const executed = evaluateV2CandidateForTier(directOnly, evidence(directOnly.id, V2_LANES[0]));
  assert.notEqual(executed.tier, "integration-recommended");
});

test("shortlists are stable and near ties prefer the smaller runtime surface", () => {
  const source = catalog();
  const lane = source.catalog.lanes[0];
  const items = lane.candidateIds.slice(0, 3).map((id, index) => evidence(id, lane.id, "service", {
    scores: scores(index === 0 ? 1 : 0),
    runtimeSurface: { services: 0, nativeBinaries: 0, dependencies: index === 0 ? 20 : index + 1 },
  }));
  const shortlist = selectV2LaneShortlist(source, items).find((item) => item.laneId === lane.id);
  assert.deepEqual(shortlist.candidateIds, [lane.candidateIds[1], lane.candidateIds[2], lane.candidateIds[0]]);
  assert.deepEqual(selectV2LaneShortlist(source, [...items, items[0]]), []);

  const commercialLane = source.catalog.lanes.at(-1);
  const commercialEvidence = commercialLane.candidateIds.slice(0, 3).map((id) => evidence(id, commercialLane.id, "service"));
  assert.deepEqual(selectV2LaneShortlist(source, commercialEvidence).find((item) => item.laneId === commercialLane.id).candidateIds, []);
});

test("all validators and evaluators are byte-stable for twenty runs and preserve inputs", () => {
  const source = catalog();
  const sourceText = canonicalizeJsonValue(source);
  const sourceBefore = canonicalizeJsonValue(source);
  const candidateInput = source.catalog.candidates[0];
  const evidenceInput = evidence(candidateInput.id, V2_LANES[0]);
  const evidenceBefore = canonicalizeJsonValue(evidenceInput);
  const outputs = Array.from({ length: 20 }, () => canonicalizeJsonValue({
    catalog: validateV2CandidateCatalogJson(sourceText),
    landscape: validateV2DecisionLandscapeJson(canonicalizeJsonValue(landscape())),
    roadmap: validateV2RoadmapJson(canonicalizeJsonValue(roadmap())),
    evaluation: evaluateV2CandidateForTier(candidateInput, evidenceInput),
  }));
  assert.equal(new Set(outputs).size, 1);
  assert.equal(canonicalizeJsonValue(source), sourceBefore);
  assert.equal(canonicalizeJsonValue(evidenceInput), evidenceBefore);
});
