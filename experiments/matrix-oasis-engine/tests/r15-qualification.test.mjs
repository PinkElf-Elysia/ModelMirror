import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  parseR15QualificationArguments,
  qualifyR15RuntimeEvidence,
  R15_COMPATIBILITY_OPERATIONAL_RETRY_LIMIT,
  R15_COMPATIBILITY_VERIFICATION_LIMIT,
  r15PhysicalRejectionCandidate,
  r15PhysicalScreenFailure,
  selectR15VerifiedSpatialCandidate,
} from "../scripts/qualify-r15-runtime-evidence.mjs";

const SOURCE_RUN_ID = `${"a".repeat(64)}-${"b".repeat(64)}`;
const hash = (character) => `sha256:${character.repeat(64)}`;
const digest = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const identity = Object.freeze({
  runtimePackSha256: hash("1"), runtimeReceiptSha256: hash("2"), environmentFactsSha256: hash("3"),
  spatialIntentSha256: hash("4"), assetBundleSha256: hash("5"), spatialSolutionSha256: hash("6"),
  spatialVerificationSha256: hash("7"),
});
function planJson() {
  return canonicalizeJsonValue({
    format: "matrix-oasis.prototype-runtime-replay-plan", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", identity,
    profile: { id: "matrix-oasis.runtime-replay/1", maxReplays: 32, maxActionsPerReplay: 256, maxSemanticStates: 100000 },
    coverage: { declaredEndingCount: 1, reachableEndingCount: 1, activeNodeCount: 1, coveredNodeCount: 1,
      loop: "not-applicable", disabledAction: "not-applicable" },
    replays: [{ id: "replay-0001", kind: "ending", actionIds: ["action-end"], probeActionId: null,
      targetId: "ending-done", resetAfter: false, expectedLocationIds: ["node-entry", "ending-done"] }],
  });
}
function evidence(replayPlanJson) {
  return {
    format: "matrix-oasis.prototype-runtime-evidence", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", replayPlanSha256: digest(Buffer.from(replayPlanJson, "utf8")),
    identity, attempt: 0, status: "passed",
    observations: [{ replayId: "replay-0001", kind: "ending", outcome: "passed", checkpoints: [{
      sequence: 0, locationKind: "node", locationId: "node-entry", stepCount: 0, actionId: null,
      playerPositionMm: [0, 900, 0], floorDistanceMm: 0, capsuleClear: true, navigationPathComplete: true,
      focusedActionId: null, interactionDistanceMm: null, visiblePlacementIds: [],
    }] }],
    performance: { sampleCount: 300, medianFrameMicros: 16667, medianFpsMilli: 60000 },
    media: { screenshots: [], videos: [] }, repairs: [],
  };
}
function source() {
  const replayPlanJson = planJson();
  return Object.freeze({
    runtimeGamePackJson: "runtime", runtimeReceiptJson: "receipt", environmentFactsJson: "facts",
    spatialIntentJson: "intent", assetBundleJson: "assets", spatialSolutionJson: "solution",
    spatialVerificationReportJson: "verification", replayPlanJson, previewFiles: new Map(),
    spatialAssemblyJson: JSON.stringify({ format: "matrix-oasis.prototype-spatial-assembly", formatVersion: "0.1.0",
      transforms: { eulerOrder: "YXZ", root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] },
        walkableEnvelope: { minimumMm: [-5000, 0, -5000], maximumMm: [5000, 4000, 5000],
          wallThicknessMm: 700, binSizeMm: 250, lateralBandMm: 4000 } } }),
    environmentColliderBytes: Uint8Array.of(1), environmentSplatBytes: Uint8Array.of(2), assetFiles: new Map(),
  });
}

function solvedCandidate(index = 0) {
  const spatialSolution = {
    nodeContexts: [{
      zoneId: "zone-a",
      playerSpawn: { floorAnchorId: `spawn-${index}` },
      actionTerminal: {
        floorAnchorId: `terminal-${index}`,
        approachFloorAnchorId: `approach-${index}`,
        yawMilliDegrees: 0,
        actionCount: 3,
        footprint: { columns: 2 },
      },
    }],
  };
  return {
    spatialSolution,
    canonicalSpatialSolutionJson: JSON.stringify(spatialSolution),
  };
}

function exactApproachCandidate(approachFloorAnchorId) {
  const spatialSolution = { nodeContexts: [{
    nodeId: "node-a", zoneId: "zone-a", playerSpawn: { floorAnchorId: "spawn-a" },
    actionTerminal: { floorAnchorId: "terminal-a", approachFloorAnchorId, positionMm: [0, 0, 0],
      yawMilliDegrees: 0, actionCount: 2, footprint: { columns: 1 } },
  }] };
  return { spatialSolution, canonicalSpatialSolutionJson: JSON.stringify(spatialSolution) };
}

test("R15 preserves the frozen adaptive terminal rejection key during physical screening", () => {
  const solved = exactApproachCandidate("approach-invalid");
  const rejection = r15PhysicalRejectionCandidate({ solved, diagnostics: [
    { code: "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED", path: "/nodeContexts/0/actionTerminal" },
  ] });
  assert.equal(rejection.kind, "station");
  assert.match(rejection.key, /^terminal\0zone-a\0terminal-a\0/u);
  assert.deepEqual(rejection.key.split("\0").slice(-2), ["2", "1"]);
});

test("historical compatibility verifies the exact frozen solution before generating candidates", async () => {
  const initial = solvedCandidate(0); let solveCount = 0;
  const result = await selectR15VerifiedSpatialCandidate({
    initialSolutionJson: initial.canonicalSpatialSolutionJson,
  }, {
    solve: async () => { solveCount += 1; throw new Error("must not solve"); },
    verify: async () => ({ ok: true, canonicalVerificationReportJson: "verified-exact" }),
  });
  assert.equal(result.ok, true);
  assert.equal(result.canonicalSpatialSolutionJson, initial.canonicalSpatialSolutionJson);
  assert.equal(result.canonicalVerificationReportJson, "verified-exact");
  assert.equal(result.verificationCount, 1);
  assert.equal(solveCount, 0);
});

test("compatibility screening records physically invalid candidates in the shared repair budget", async () => {
  const initial = solvedCandidate(0); let solveCount = 0; let verifyCount = 0;
  const result = await selectR15VerifiedSpatialCandidate({
    initialSolutionJson: initial.canonicalSpatialSolutionJson,
  }, {
    solve: async ({ rejectedStations }) => {
      solveCount += 1;
      assert.equal(rejectedStations.size, solveCount);
      return { ok: true, ...solvedCandidate(solveCount) };
    },
    verify: async () => {
      verifyCount += 1;
      return verifyCount === 3
        ? { ok: true, canonicalVerificationReportJson: "verified-screened" }
        : { ok: false, diagnostics: [{ code: "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", path: "/nodeContexts/0" }] };
    },
  });
  assert.equal(result.ok, true);
  assert.equal(result.canonicalVerificationReportJson, "verified-screened");
  assert.equal(result.verificationCount, 3);
  assert.equal(solveCount, 2);
  assert.equal(result.rejectedStations.length, 2);
  assert.deepEqual(result.repairs.map(({ round, kind }) => ({ round, kind })), [
    { round: 1, kind: "station" }, { round: 2, kind: "station" },
  ]);
  assert.ok(result.repairs.every((item) => /^sha256:[0-9a-f]{64}$/u.test(item.candidateKeySha256)));
});

test("compatibility screening is bounded and returns a static failure without a hidden extra candidate", async () => {
  const initial = solvedCandidate(0); let solveCount = 0; let verifyCount = 0;
  const result = await selectR15VerifiedSpatialCandidate({
    initialSolutionJson: initial.canonicalSpatialSolutionJson,
  }, {
    solve: async () => ({ ok: true, ...solvedCandidate(++solveCount) }),
    verify: async () => {
      verifyCount += 1;
      return { ok: false, diagnostics: [{ code: "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", path: "/nodeContexts/0" }] };
    },
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "R15_COMPATIBILITY_VERIFICATION_LIMIT_EXCEEDED");
  assert.equal(verifyCount, R15_COMPATIBILITY_VERIFICATION_LIMIT);
  assert.equal(solveCount, R15_COMPATIBILITY_VERIFICATION_LIMIT - 1);
});

test("compatibility screening allows the frozen candidate plus at most two repairs", () => {
  assert.equal(R15_COMPATIBILITY_VERIFICATION_LIMIT, 3);
  assert.equal(R15_COMPATIBILITY_OPERATIONAL_RETRY_LIMIT, 1);
});

test("compatibility screening retries one identical operational verification without rejecting it", async () => {
  const initial = solvedCandidate(0); let verifyCount = 0;
  const result = await selectR15VerifiedSpatialCandidate({
    initialSolutionJson: initial.canonicalSpatialSolutionJson,
  }, {
    solve: async () => assert.fail("must not solve"),
    verify: async (json) => {
      verifyCount += 1;
      assert.equal(json, initial.canonicalSpatialSolutionJson);
      if (verifyCount === 1) throw new Error("sensitive");
      return { ok: true, canonicalVerificationReportJson: "verified-after-retry" };
    },
  });
  assert.equal(result.ok, true);
  assert.equal(result.verificationCount, 1);
  assert.equal(verifyCount, 2);
});

test("compatibility screening fails closed after one identical operational retry", async () => {
  const initial = solvedCandidate(0); let verifyCount = 0;
  const result = await selectR15VerifiedSpatialCandidate({
    initialSolutionJson: initial.canonicalSpatialSolutionJson,
  }, {
    solve: async () => assert.fail("must not solve"),
    verify: async () => { verifyCount += 1; throw new Error("sensitive"); },
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "R15_COMPATIBILITY_VERIFIER_INTERNAL_ERROR");
  assert.equal(verifyCount, 2);
  assert.doesNotMatch(JSON.stringify(result), /sensitive/u);
});

test("compatibility screening redacts a frozen solver exception into a static stage code", async () => {
  const initial = solvedCandidate(0);
  const result = await selectR15VerifiedSpatialCandidate({
    initialSolutionJson: initial.canonicalSpatialSolutionJson,
  }, {
    solve: async () => { throw new Error("sensitive-solver-detail"); },
    verify: async () => ({ ok: false, diagnostics: [
      { code: "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", path: "/nodeContexts/0" },
    ] }),
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "R15_COMPATIBILITY_SOLVER_INTERNAL_ERROR");
  assert.doesNotMatch(JSON.stringify(result), /sensitive-solver-detail/u);
});

test("qualification arguments require four direct temporary roots and an exact source run id", () => {
  const root = path.resolve(path.parse(process.cwd()).root, "tmp");
  const parsed = parseR15QualificationArguments([
    "--prototype-run-root", path.join(root, "prototype"), "--spatial-run-root", path.join(root, "spatial"),
    "--solved-run-root", path.join(root, "solved"), "--evidence-run-root", path.join(root, "evidence"),
    "--run-id", SOURCE_RUN_ID,
  ], root);
  assert.equal(parsed.runId, SOURCE_RUN_ID);
  assert.throws(() => parseR15QualificationArguments([
    "--prototype-run-root", path.join(root, "nested", "prototype"), "--spatial-run-root", path.join(root, "spatial"),
    "--solved-run-root", path.join(root, "solved"), "--evidence-run-root", path.join(root, "evidence"),
    "--run-id", SOURCE_RUN_ID,
  ], root), (error) => error.code === "R15_QUALIFICATION_ARGUMENT_INVALID");
});

test("runtime repair physical screening exposes only a bounded static stage", () => {
  const cases = [
    ["R15_COMPATIBILITY_VERIFICATION_LIMIT_EXCEEDED", "R15_RESOLVE_PHYSICAL_SCREEN_LIMIT_EXCEEDED"],
    ["R15_COMPATIBILITY_CANDIDATE_REPEATED", "R15_RESOLVE_PHYSICAL_CANDIDATE_REPEATED"],
    ["R15_COMPATIBILITY_VERIFIER_INTERNAL_ERROR", "R15_RESOLVE_PHYSICAL_VERIFIER_INTERNAL_ERROR"],
    ["PROTOTYPE_SPATIAL_SOLVER_NO_SOLUTION", "R15_RESOLVE_PHYSICAL_SOLVE_FAILED"],
    ["PROTOTYPE_SPATIAL_VERIFY_UNKNOWN", "R15_RESOLVE_PHYSICAL_REJECTION_UNMAPPED"],
  ];
  for (const [code, message] of cases) {
    const result = r15PhysicalScreenFailure({ diagnostics: [{ code, untrustedField: "must-not-leak" }] });
    assert.deepEqual(result.diagnostics, [{
      phase: "qualification", severity: "error", code: "R15_QUALIFICATION_RESOLVE_FAILED", path: "", message,
    }]);
  }
});

test("zero-network qualification collects, verifies and publishes one cache-bound evidence result", async () => {
  const observed = []; const input = source();
  const result = await qualifyR15RuntimeEvidence({
    prototypeRunRoot: "prototype", spatialRunRoot: "spatial", solvedRunRoot: "solved", evidenceRunRoot: "evidence",
    temporaryRoot: "temporary", runId: SOURCE_RUN_ID, godotBin: path.resolve("godot.exe"),
  }, {
    loadSource: async () => ({ ok: true, source: input }),
    createEvidenceRunner: () => Object.freeze({ runner: true }), createVerifier: () => Object.freeze({ verifier: true }),
    collect: async (request, runner) => { observed.push(["collect", request, runner]); return {
      ok: true, evidence: evidence(input.replayPlanJson), mediaFiles: new Map(),
    }; },
    publish: async (request) => { observed.push(["publish", request]); return { runId: "c".repeat(64) }; },
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.attempt, 0);
  assert.deepEqual(observed.map(([name]) => name), ["collect", "publish"]);
  assert.equal(observed[1][1].previewFiles, input.previewFiles);
});

test("compatibility repairs consume the same two-round runtime evidence budget", async () => {
  const input = source(); let published;
  const usedRepair = Object.freeze({
    round: 1, kind: "station", candidateKeySha256: hash("a"),
    diagnosticCode: "R15_STATION_RUNTIME_INVALID",
  });
  const result = await qualifyR15RuntimeEvidence({
    prototypeRunRoot: "prototype", spatialRunRoot: "spatial", solvedRunRoot: "solved",
    evidenceRunRoot: "evidence", temporaryRoot: "temporary", runId: SOURCE_RUN_ID,
    godotBin: path.resolve("godot.exe"),
  }, {
    loadSource: async () => ({ ok: true, source: input, compatibilityRepairs: [usedRepair] }),
    createEvidenceRunner: () => Object.freeze({ runner: true }),
    createVerifier: () => Object.freeze({ verifier: true }),
    collect: async () => ({ ok: true, evidence: evidence(input.replayPlanJson), mediaFiles: new Map() }),
    publish: async (request) => { published = JSON.parse(request.canonicalEvidenceJson); return { runId: "d".repeat(64) }; },
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.attempt, 1);
  assert.deepEqual(result.repairs, [usedRepair]);
  assert.equal(published.attempt, 1);
  assert.deepEqual(published.repairs, [usedRepair]);
});

test("an authorized historical overlay must complete real runtime preflight before publication", async () => {
  const input = source(); let collected = 0; let published = 0;
  const preflight = { ok: true, evidence: evidence(input.replayPlanJson), mediaFiles: new Map() };
  const result = await qualifyR15RuntimeEvidence({
    prototypeRunRoot: "prototype", spatialRunRoot: "spatial", solvedRunRoot: "solved", evidenceRunRoot: "evidence",
    temporaryRoot: "temporary", runId: SOURCE_RUN_ID, godotBin: path.resolve("godot.exe"),
  }, {
    loadSource: async () => ({ ok: true, source: input, preflight }),
    createEvidenceRunner: () => Object.freeze({ runner: true }), createVerifier: () => Object.freeze({ verifier: true }),
    collect: async () => { collected += 1; throw new Error("preflight must be consumed first"); },
    publish: async () => { published += 1; return { runId: "d".repeat(64) }; },
  });
  assert.equal(result.ok, true);
  assert.equal(collected, 0);
  assert.equal(published, 1);
});

test("qualification implementation has no provider or network call surface", async () => {
  const sourceText = await readFile(new URL("../scripts/qualify-r15-runtime-evidence.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(sourceText, /\b(?:fetch|https?|OpenAI|Marble|Meshy|provider)\b/u);
  assert.match(sourceText, /loadVerifiedSolvedSpatialPrototypeRun/u);
  assert.match(sourceText, /historicalOverlay[\s\S]+collectPrototypeRuntimeEvidence/u);
  assert.match(sourceText, /solvePrototypeSpatialLayoutInternal/u);
  assert.match(sourceText, /verifyPrototypeSpatialSolution/u);
});
