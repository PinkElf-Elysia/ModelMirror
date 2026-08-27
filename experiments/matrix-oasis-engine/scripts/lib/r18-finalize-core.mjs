import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  evaluateV2CandidateForTier,
  selectV2LaneShortlist,
  validateV2DecisionLandscapeJson,
  validateV2RoadmapJson,
} from "@matrix-oasis/v2-landscape-contracts";
import { verifyR18QualificationEvidenceLock } from "./r18-evidence-import-core.mjs";
import { buildR18DesktopLandscape, R18LandscapeError } from "./r18-landscape-core.mjs";

const DECISION_PATH = "docs/R18_DECISION_LANDSCAPE.json";
const ROADMAP_PATH = "docs/R18_ROADMAP.json";

const LANE_SWITCH = Object.freeze({
  "npc-orchestration": Object.freeze({ code: "SWITCH_IF_ADJUDICATION_TRACE_DIVERGES", observable: "Switch when identical NPC intents yield different verdicts or mutate authoritative state before adjudication." }),
  "memory-relationships": Object.freeze({ code: "SWITCH_IF_LEDGER_REBUILD_DIVERGES", observable: "Switch when correction, deletion, session isolation, or full Ledger rebuild produces a different memory projection." }),
  "dynamic-events": Object.freeze({ code: "SWITCH_IF_EVENT_PROPOSAL_MUTATES_RUNTIME", observable: "Switch when an event candidate changes Runtime before adjudication or leaves a partial event after failure." }),
  "godot-behavior": Object.freeze({ code: "SWITCH_IF_GODOT_BEHAVIOR_GATE_FAILS", observable: "Switch when abort, timeout, blackboard isolation, deterministic trace, or the 64-agent Godot profile fails." }),
  "dialogue-presentation": Object.freeze({ code: "SWITCH_IF_DIALOGUE_SANDBOX_LEAKS", observable: "Switch when dialogue can execute expressions, mutate state, load dynamic resources, or fails reset and localization." }),
  "character-animation": Object.freeze({ code: "SWITCH_IF_CHARACTER_PROFILE_FAILS", observable: "Switch when idle, walk, turn, skeleton, foot contact, AABB, import identity, or 300-frame performance fails." }),
  "evaluation-observability": Object.freeze({ code: "SWITCH_IF_REPLAY_EVIDENCE_DIVERGES", observable: "Switch when fixed input replay, state diff, node evidence, or report identity is incomplete or non-deterministic." }),
  "creator-commercial-benchmark": Object.freeze({ code: "SWITCH_IF_PUBLIC_BENCHMARK_DRIFTS", observable: "Reassess when an official public capability, pricing, ownership, privacy, or export claim materially changes." }),
});

function fail(code) {
  throw new R18LandscapeError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function total(scores) {
  return Object.values(scores).reduce((sum, value) => sum + value, 0);
}

function mergeHardGates(decision, evidence) {
  const failureGate = {
    internal: "fail-closed",
    service: "fail-closed",
    "embedded-godot": "runtime-compatibility",
    asset: "runtime-compatibility",
    commercial: "public-evidence",
  }[decision.qualificationClass];
  return decision.hardGates.map((gate) => {
    if (gate.id === "source-identity") return { ...gate, status: evidence.sourceIdentityStatus, code: null };
    if (evidence.status === "failed" && evidence.harnessAttribution === "candidate" && gate.id === failureGate) {
      return { ...gate, status: "fail", code: "R18_CANDIDATE_RUNTIME_FIXTURE_FAILED" };
    }
    return { ...gate };
  });
}

function switchConditions(decision, evidence) {
  const conditions = [{ ...LANE_SWITCH[decision.laneId] }];
  if (evidence?.sourceIdentityStatus === "not-proven") conditions.push({ code: "SWITCH_IF_SOURCE_IDENTITY_UNPROVEN", observable: "Do not integrate until exact checkout, tree, archive, and direct license evidence reproduce without approximation." });
  if (evidence?.status === "evidence-gap" || (evidence?.status === "failed" && evidence.harnessAttribution !== "candidate")) conditions.push({ code: "SWITCH_IF_QUALIFICATION_GAP_PERSISTS", observable: "Use the next ranked candidate or the explicit internal build boundary if the named qualification gap remains unresolved." });
  return conditions;
}

function mergeEvidence(decision, evidence) {
  if (!evidence) {
    return {
      candidateId: decision.candidateId,
      laneId: decision.laneId,
      qualificationClass: decision.qualificationClass,
      executionStatus: decision.executionStatus,
      harnessAttribution: decision.harnessAttribution,
      hardGates: decision.hardGates.map((gate) => ({ ...gate })),
      scores: { ...decision.scores },
      runtimeSurface: { ...decision.runtimeSurface },
      switchConditions: switchConditions(decision, null),
      evidenceSha256: [...decision.evidenceSha256],
      attempted: false,
    };
  }
  const attempted = evidence.laneIds.includes(decision.laneId);
  const globalEvidenceHashes = [...new Set([...decision.evidenceSha256, evidence.reportSha256])].sort();
  if (!attempted) {
    const sourceOnlyEvidence = { ...evidence, status: "planned" };
    return {
      candidateId: decision.candidateId,
      laneId: decision.laneId,
      qualificationClass: decision.qualificationClass,
      executionStatus: decision.executionStatus,
      harnessAttribution: decision.harnessAttribution,
      hardGates: mergeHardGates(decision, sourceOnlyEvidence),
      scores: { ...decision.scores },
      runtimeSurface: { ...decision.runtimeSurface },
      switchConditions: switchConditions(decision, sourceOnlyEvidence),
      evidenceSha256: globalEvidenceHashes,
      attempted: false,
    };
  }
  return {
    candidateId: decision.candidateId,
    laneId: decision.laneId,
    qualificationClass: decision.qualificationClass,
    executionStatus: evidence.status,
    harnessAttribution: evidence.harnessAttribution,
    hardGates: mergeHardGates(decision, evidence),
    scores: { ...decision.scores },
    runtimeSurface: { ...decision.runtimeSurface },
    switchConditions: switchConditions(decision, evidence),
    evidenceSha256: [...new Set([
      ...decision.evidenceSha256,
      evidence.planSha256,
      evidence.executionEvidenceSha256,
      evidence.reportSha256,
      ...evidence.fixtureOutcomes.map((fixture) => fixture.traceSha256),
    ])].sort(),
    attempted: true,
  };
}

function roadmap(decisionLandscapeSha256) {
  return {
    format: "matrix-oasis.v2-roadmap",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    decisionLandscapeSha256,
    rounds: [
      {
        id: "R19",
        objective: "Freeze provider-neutral NPC Intent, World Event Ledger, Adjudication Result, Memory Projection, relationship, provenance, version and replay contracts without model calls.",
        dependsOn: ["R18"],
        entryGates: ["R18_LANDSCAPE_QUALIFIED", "R18_AUTHORITY_BOUNDARY_SELECTED"],
        exitGates: ["R19_ADJUDICATION_FAIL_CLOSED", "R19_LEDGER_REBUILD_DETERMINISTIC", "R19_CONTRACTS_CANONICAL"],
        prohibited: ["R19_MODEL_CALL_FORBIDDEN", "R19_PRODUCT_GODOT_CHANGE_FORBIDDEN", "R19_RUNTIME_AUTHORITY_DUPLICATION_FORBIDDEN"],
        rollback: "Revert the isolated R19 contracts and fixtures; R16 MVP Runtime and all R18 evidence remain authoritative and unchanged.",
      },
      {
        id: "R20",
        objective: "Implement deterministic NPC policy execution and a Godot entity bridge against R19 intents and verdicts, with fixed strategies and no long-term memory dependency.",
        dependsOn: ["R19"],
        entryGates: ["R19_ADJUDICATION_FAIL_CLOSED", "R19_LEDGER_REBUILD_DETERMINISTIC"],
        exitGates: ["R20_GODOT_ENTITY_BRIDGE_QUALIFIED", "R20_MULTI_AGENT_TRACE_DETERMINISTIC", "R20_RUNTIME_REMAINS_AUTHORITATIVE"],
        prohibited: ["R20_LONG_TERM_MEMORY_FORBIDDEN", "R20_DYNAMIC_EVENT_GENERATION_FORBIDDEN", "R20_MODEL_CALL_FORBIDDEN"],
        rollback: "Disable the R20 profile and revert its bridge; deterministic Runtime and R19 contracts remain usable without NPC execution.",
      },
      {
        id: "R21",
        objective: "Add persona, long-term memory and relationship projections derived from the World Event Ledger, with deletion, correction, isolation and full rebuild guarantees.",
        dependsOn: ["R20"],
        entryGates: ["R20_GODOT_ENTITY_BRIDGE_QUALIFIED", "R20_RUNTIME_REMAINS_AUTHORITATIVE"],
        exitGates: ["R21_LEDGER_REBUILD_EQUIVALENT", "R21_MEMORY_DELETION_VERIFIED", "R21_RELATIONSHIP_PROJECTION_DETERMINISTIC"],
        prohibited: ["R21_MEMORY_AUTHORITY_FORBIDDEN", "R21_EXTERNAL_MODEL_CALL_FORBIDDEN", "R21_DYNAMIC_TASKS_FORBIDDEN"],
        rollback: "Delete and rebuild the derived memory index from Ledger events, then disable the R21 projection profile without changing world history.",
      },
      {
        id: "R22",
        objective: "Introduce a bounded AI dialogue and cognition loop with per-call approval, budgets, timeout, deterministic fallback, redacted evidence and authoritative action adjudication.",
        dependsOn: ["R21"],
        entryGates: ["R21_LEDGER_REBUILD_EQUIVALENT", "R21_MEMORY_DELETION_VERIFIED"],
        exitGates: ["R22_DIALOGUE_BUDGET_ENFORCED", "R22_FALLBACK_PLAYABLE", "R22_UNTRUSTED_OUTPUT_ADJUDICATED"],
        prohibited: ["R22_UNBOUNDED_AGENT_LOOP_FORBIDDEN", "R22_MODEL_DIRECT_RUNTIME_MUTATION_FORBIDDEN", "R22_HIDDEN_PROVIDER_RETRY_FORBIDDEN"],
        rollback: "Disable the AI cognition profile and retain fixed-policy NPC dialogue backed by the same R19-R21 contracts and Ledger state.",
      },
      {
        id: "R23",
        objective: "Add AI task and world-event proposals that must be adjudicated, committed atomically, rolled back on failure and proven by runtime replay evidence.",
        dependsOn: ["R22"],
        entryGates: ["R22_FALLBACK_PLAYABLE", "R22_UNTRUSTED_OUTPUT_ADJUDICATED"],
        exitGates: ["R23_EVENT_COMMIT_ATOMIC", "R23_EVENT_REPLAY_EQUIVALENT", "R23_PARTIAL_EVENT_IMPOSSIBLE"],
        prohibited: ["R23_PROPOSAL_AS_AUTHORITY_FORBIDDEN", "R23_UNBOUNDED_WORLD_EVENT_FORBIDDEN", "R23_ASSET_REGENERATION_FORBIDDEN"],
        rollback: "Disable event proposal intake and replay the authoritative Ledger to the last committed verdict; no half-event may remain.",
      },
      {
        id: "R24",
        objective: "Expose the qualified V2 profile in Creator so natural language can produce bounded NPC, relationship and event configuration while preserving the R16 one-prompt 3D path.",
        dependsOn: ["R23"],
        entryGates: ["R23_EVENT_COMMIT_ATOMIC", "R23_EVENT_REPLAY_EQUIVALENT"],
        exitGates: ["R24_CREATOR_V2_PROFILE_QUALIFIED", "R24_FAILURE_PRESERVES_MVP", "R24_APPROVALS_CONTENT_BOUND"],
        prohibited: ["R24_DEFAULT_PROFILE_SWITCH_FORBIDDEN", "R24_PARENT_PRODUCT_INTEGRATION_FORBIDDEN", "R24_UNBOUNDED_CONFIGURATION_FORBIDDEN"],
        rollback: "Switch Creator back to the R16 MVP profile; preserve qualified V2 runs as isolated caches and leave the default product path unchanged.",
      },
      {
        id: "R25",
        objective: "Qualify V2 across multiple real cases for correctness, experience, safety, latency, cost and commercial value before any second-version completion claim.",
        dependsOn: ["R24"],
        entryGates: ["R24_CREATOR_V2_PROFILE_QUALIFIED", "R24_FAILURE_PRESERVES_MVP"],
        exitGates: ["R25_MULTI_CASE_QUALIFIED", "R25_COST_VALUE_ACCEPTED", "R25_HUMAN_ACCEPTANCE_RECORDED"],
        prohibited: ["R25_SINGLE_CASE_CLAIM_FORBIDDEN", "R25_UNVERIFIED_PRODUCT_CLAIM_FORBIDDEN", "R25_AUTOMATIC_EXTERNAL_SPEND_FORBIDDEN"],
        rollback: "Keep V2 claimAllowed false and retain R16 as the public MVP while reverting only the R25 qualification/default-switch commit.",
      },
    ],
  };
}

export function buildR18FinalLandscape({ moduleRoot }) {
  const desktop = buildR18DesktopLandscape({ moduleRoot });
  const qualification = verifyR18QualificationEvidenceLock({ moduleRoot });
  const evidenceByCandidate = new Map(qualification.entries.map((entry) => [entry.candidateId, entry]));
  const candidateById = new Map(desktop.catalog.catalog.candidates.map((candidate) => [candidate.id, candidate]));
  const merged = desktop.audit.decisions.map((decision) => mergeEvidence(decision, evidenceByCandidate.get(decision.candidateId)));
  const shortlists = selectV2LaneShortlist(desktop.catalog, merged, { maximumPerLane: 3 });
  const ranks = new Map(shortlists.flatMap((lane) => lane.candidateIds.map((candidateId, index) => [`${lane.laneId}\0${candidateId}`, index + 1])));
  const decisions = merged.map((item) => {
    const candidate = candidateById.get(item.candidateId);
    const evaluated = evaluateV2CandidateForTier(candidate, item);
    const rank = ranks.get(`${item.laneId}\0${item.candidateId}`) ?? null;
    return {
      candidateId: item.candidateId,
      laneId: item.laneId,
      qualificationClass: item.qualificationClass,
      executionStatus: item.executionStatus,
      harnessAttribution: item.harnessAttribution,
      tier: evaluated.tier,
      conclusion: evaluated.conclusion,
      confidence: item.attempted && item.executionStatus !== "planned" ? (item.hardGates.some((gate) => gate.status === "fail") ? "high" : "medium") : item.qualificationClass === "commercial" ? "medium" : "low",
      hardGates: item.hardGates,
      scores: item.scores,
      total: total(item.scores),
      runtimeSurface: item.runtimeSurface,
      shortlistRank: rank,
      evidenceSha256: item.evidenceSha256,
      switchConditions: item.switchConditions,
      exclusionCode: evaluated.conclusion === "rejected" ? candidate.staticExclusion.code || "R18_CANDIDATE_RUNTIME_FIXTURE_FAILED" : null,
    };
  });
  const landscape = {
    format: "matrix-oasis.v2-decision-landscape",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    catalogSha256: desktop.audit.catalogSha256,
    policy: { shortlistMinimumScore: 70, integrationMinimumScore: 80, nearTieScoreDelta: 5, minimumExecutedCandidates: 12, maximumExecutedCandidates: 16 },
    decisions,
  };
  const landscapeText = canonicalizeJsonValue(landscape);
  const landscapeValidation = validateV2DecisionLandscapeJson(landscapeText);
  if (!landscapeValidation.valid) throw Object.assign(new R18LandscapeError("R18_FINAL_LANDSCAPE_INVALID"), { diagnosticCodes: landscapeValidation.diagnostics.map((diagnostic) => diagnostic.code) });
  const roadmapValue = roadmap(sha256(Buffer.from(landscapeText, "utf8")));
  const roadmapText = canonicalizeJsonValue(roadmapValue);
  const roadmapValidation = validateV2RoadmapJson(roadmapText);
  if (!roadmapValidation.valid) throw Object.assign(new R18LandscapeError("R18_FINAL_ROADMAP_INVALID"), { diagnosticCodes: roadmapValidation.diagnostics.map((diagnostic) => diagnostic.code) });
  return Object.freeze({ landscape, landscapeText, roadmap: roadmapValue, roadmapText, shortlists });
}

export function verifyR18FinalLandscape({ moduleRoot }) {
  const built = buildR18FinalLandscape({ moduleRoot });
  const landscapeText = fs.readFileSync(path.join(moduleRoot, ...DECISION_PATH.split("/")), "utf8");
  const roadmapText = fs.readFileSync(path.join(moduleRoot, ...ROADMAP_PATH.split("/")), "utf8");
  if (landscapeText !== built.landscapeText || roadmapText !== built.roadmapText) fail("R18_FINAL_TRACKED_OUTPUT_DRIFT");
  return Object.freeze({
    decisions: built.landscape.decisions.length,
    attemptedCandidates: new Set(built.landscape.decisions.filter((decision) => ["executed", "failed", "evidence-gap"].includes(decision.executionStatus)).map((decision) => decision.candidateId)).size,
    integrationRecommended: built.landscape.decisions.filter((decision) => decision.tier === "integration-recommended").length,
    landscapeSha256: sha256(Buffer.from(built.landscapeText, "utf8")),
    roadmapSha256: sha256(Buffer.from(built.roadmapText, "utf8")),
  });
}

export const R18_FINAL_OUTPUTS = Object.freeze({ decision: DECISION_PATH, roadmap: ROADMAP_PATH });
