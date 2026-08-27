import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  validateV2DecisionLandscapeJson,
  validateV2RoadmapJson,
} from "@matrix-oasis/v2-landscape-contracts";
import {
  buildR18FinalLandscape,
  verifyR18FinalLandscape,
} from "../scripts/lib/r18-finalize-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function read(relative) {
  return readFileSync(path.join(moduleRoot, ...relative.split("/")), "utf8");
}

const expectedShortlists = Object.freeze({
  "character-animation": ["kenney-animated-characters-retro", "static-character-asset-baseline"],
  "dialogue-presentation": ["native-control-dialogue-baseline", "dialogue-manager"],
  "dynamic-events": ["world-event-ledger-baseline", "deterministic-runtime-baseline", "voyager"],
  "evaluation-observability": ["world-event-ledger-baseline", "creator-qualification-baseline", "runtime-evidence-baseline"],
  "godot-behavior": ["deterministic-runtime-baseline", "beehave", "limboai"],
  "memory-relationships": ["world-event-ledger-baseline", "mem0"],
  "npc-orchestration": ["world-event-ledger-baseline", "deterministic-runtime-baseline", "sotopia"],
});

const laneSwitchCode = Object.freeze({
  "character-animation": "SWITCH_IF_CHARACTER_PROFILE_FAILS",
  "dialogue-presentation": "SWITCH_IF_DIALOGUE_SANDBOX_LEAKS",
  "dynamic-events": "SWITCH_IF_EVENT_PROPOSAL_MUTATES_RUNTIME",
  "evaluation-observability": "SWITCH_IF_REPLAY_EVIDENCE_DIVERGES",
  "godot-behavior": "SWITCH_IF_GODOT_BEHAVIOR_GATE_FAILS",
  "memory-relationships": "SWITCH_IF_LEDGER_REBUILD_DIVERGES",
  "npc-orchestration": "SWITCH_IF_ADJUDICATION_TRACE_DIVERGES",
});

test("tracked final landscape and roadmap are canonical products of the locked evidence", () => {
  const report = verifyR18FinalLandscape({ moduleRoot });
  assert.deepEqual(report, {
    decisions: 96,
    attemptedCandidates: 13,
    integrationRecommended: 0,
    landscapeSha256: "65ed29270ec77aa2e64401f591e5f7fb58e93acb65456c4bf141e42195813a00",
    roadmapSha256: "8ecf5d2a5b2e4f5fea3ac64960949ce56b7a095651bcaba96bef42a4b927b428",
  });
  assert.equal(validateV2DecisionLandscapeJson(read("docs/R18_DECISION_LANDSCAPE.json")).valid, true);
  assert.equal(validateV2RoadmapJson(read("docs/R18_ROADMAP.json")).valid, true);
});

test("each executable lane keeps two or three ranked next actions without promoting evidence gaps", () => {
  const built = buildR18FinalLandscape({ moduleRoot });
  for (const [laneId, candidateIds] of Object.entries(expectedShortlists)) {
    assert.deepEqual(built.shortlists.find((item) => item.laneId === laneId).candidateIds, candidateIds);
    const ranked = built.landscape.decisions
      .filter((item) => item.laneId === laneId && item.shortlistRank !== null)
      .sort((left, right) => left.shortlistRank - right.shortlistRank);
    assert.deepEqual(ranked.map((item) => item.candidateId), candidateIds);
    assert.ok(ranked.every((item) => item.tier === "executable-shortlist"));
    assert.ok(ranked.every((item) => item.conclusion !== "recommended"));
    assert.ok(ranked.every((item) => item.switchConditions[0].code === laneSwitchCode[laneId]));
  }
  assert.equal(built.shortlists.find((item) => item.laneId === "creator-commercial-benchmark").candidateIds.length, 0);
});

test("attempted evidence gaps and source identity gaps remain fail closed across every lane", () => {
  const { landscape } = buildR18FinalLandscape({ moduleRoot });
  const attempted = landscape.decisions.filter((item) => ["executed", "failed", "evidence-gap"].includes(item.executionStatus));
  assert.equal(new Set(attempted.map((item) => item.candidateId)).size, 13);
  assert.ok(attempted.every((item) => item.conclusion === "deferred"));
  assert.equal(landscape.decisions.some((item) => item.tier === "integration-recommended"), false);

  for (const candidateId of ["concordia", "tinytroupe"]) {
    const decisions = landscape.decisions.filter((item) => item.candidateId === candidateId);
    assert.ok(decisions.length > 1);
    assert.ok(decisions.every((item) => item.hardGates.find((gate) => gate.id === "source-identity").status === "not-proven"));
    assert.ok(decisions.every((item) => item.switchConditions.some((condition) => condition.code === "SWITCH_IF_SOURCE_IDENTITY_UNPROVEN")));
  }
});

test("roadmap freezes R19 through R25 with dependencies, prohibitions and rollback", () => {
  const { roadmap } = buildR18FinalLandscape({ moduleRoot });
  assert.deepEqual(roadmap.rounds.map((round) => round.id), ["R19", "R20", "R21", "R22", "R23", "R24", "R25"]);
  for (const [index, round] of roadmap.rounds.entries()) {
    assert.ok(round.entryGates.length > 0);
    assert.ok(round.exitGates.length > 0);
    assert.ok(round.prohibited.length > 0);
    assert.ok(round.rollback.length > 40);
    assert.deepEqual(round.dependsOn, [index === 0 ? "R18" : `R${18 + index}`]);
  }
  assert.match(roadmap.rounds[0].objective, /World Event Ledger/u);
  assert.match(roadmap.rounds.at(-1).objective, /commercial value/u);
});

test("twenty final builds are byte identical", () => {
  const first = buildR18FinalLandscape({ moduleRoot });
  for (let index = 0; index < 20; index += 1) {
    const next = buildR18FinalLandscape({ moduleRoot });
    assert.equal(next.landscapeText, first.landscapeText);
    assert.equal(next.roadmapText, first.roadmapText);
  }
});
