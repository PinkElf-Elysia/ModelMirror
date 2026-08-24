import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import path from "node:path";
import {
  createR16CreatorQualificationOperations,
  discoverR16CreatorQualificationCache,
  parseR16CreatorQualificationArguments,
  qualifyR16Creator,
} from "../scripts/lib/r16-creator-core.mjs";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { runR16CreatorQualificationCli } from "../scripts/qualify-r16-creator.mjs";

const hash = (character) => `sha256:${character.repeat(64)}`;
const temporaryRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const roots = Object.freeze({
  prototypeRunRoot: path.join(temporaryRoot, "r16-prototype"),
  spatialRunRoot: path.join(temporaryRoot, "r16-spatial"),
  solvedRunRoot: path.join(temporaryRoot, "r16-solved"),
  evidenceRunRoot: path.join(temporaryRoot, "r16-evidence"),
  qualifiedRunRoot: path.join(temporaryRoot, "r16-qualified"),
});
const baseOptions = Object.freeze({
  ...roots,
  temporaryRoot,
  godotBin: path.join(temporaryRoot, "tools", "godot.exe"),
  godotVersion: "4.6.3",
});

function source() {
  return Object.freeze({
    runId: `${"1".repeat(64)}-${"2".repeat(64)}`,
    promptSha256: hash("3"),
    model: "gpt-5.6-luna",
  });
}

function qualification(solutionSha256 = hash("4"), attempt = 0) {
  return Object.freeze({
    hashes: Object.freeze({ spatialSolutionSha256: solutionSha256 }),
    evidence: Object.freeze({ attempt, runId: "5".repeat(64) }),
  });
}

function operationsFor(level, calls, {
  initialSolution = hash("4"),
  finalSolution = initialSolution,
  failAt = null,
} = {}) {
  const operation = (name, body) => async (request) => {
    calls.push(name);
    if (name === failAt) return { ok: false };
    return await body(request);
  };
  return Object.freeze({
    analyze: operation("analyze", async () => ({ ok: true, analysis: { sourceRunId: source().runId } })),
    solve: operation("solve", async () => ({ ok: true, solved: { solutionSha256: initialSolution } })),
    verify: operation("verify", async () => ({ ok: true, verification: { solutionSha256: initialSolution } })),
    verifySolved: operation("verifySolved", async () => ({ ok: true })),
    verifyEvidence: operation("verifyEvidence", async () => ({ ok: true })),
    verifyQualified: operation("verifyQualified", async () => ({ ok: true })),
    collectEvidence: operation("collectEvidence", async ({ onAttempt }) => {
      await onAttempt(1);
      return {
        ok: true,
        attempt: 1,
        finalSolutionSha256: finalSolution,
        solved: { solutionSha256: finalSolution },
        verification: { solutionSha256: finalSolution },
        evidence: { solutionSha256: finalSolution, attempt: 1 },
      };
    }),
    publishQualification: operation("publishQualification", async ({ expectedSolutionSha256 }) => ({
      ok: true,
      qualification: qualification(expectedSolutionSha256, 1),
    })),
    network: async () => assert.fail(`${level} must not use network`),
    readCredentials: async () => assert.fail(`${level} must not read credentials`),
  });
}

function discovered(cacheLevel, solutionSha256 = hash("4")) {
  const common = { cacheLevel, source: source() };
  if (cacheLevel === "qualified") return { ...common, qualified: qualification(solutionSha256) };
  if (cacheLevel === "evidence-only") {
    return {
      ...common,
      evidence: { solutionSha256, attempt: 0 },
      solved: { solutionSha256 },
      verification: { solutionSha256 },
    };
  }
  if (cacheLevel === "solved-only") {
    return { ...common, solved: { solutionSha256 }, verification: { solutionSha256 } };
  }
  return common;
}

async function runLevel(cacheLevel, operationOptions = {}, optionOverrides = {}) {
  const calls = [];
  const stages = [];
  const result = await qualifyR16Creator({
    ...baseOptions,
    ...optionOverrides,
    onStage: (stage) => stages.push(stage),
  }, {
    discoverCache: async () => discovered(cacheLevel, operationOptions.initialSolution),
    operations: operationsFor(cacheLevel, calls, operationOptions),
  });
  return { calls, result, stages };
}

test("R16 CLI parses five direct-child roots and an optional explicit source run", () => {
  const parsed = parseR16CreatorQualificationArguments([
    "--prototype-run-root", roots.prototypeRunRoot,
    "--spatial-run-root", roots.spatialRunRoot,
    "--solved-run-root", roots.solvedRunRoot,
    "--evidence-run-root", roots.evidenceRunRoot,
    "--qualified-run-root", roots.qualifiedRunRoot,
  ], temporaryRoot);
  assert.deepEqual(parsed, { ...roots, temporaryRoot });
  assert.deepEqual(parseR16CreatorQualificationArguments([
    "--prototype-run-root", roots.prototypeRunRoot,
    "--spatial-run-root", roots.spatialRunRoot,
    "--solved-run-root", roots.solvedRunRoot,
    "--evidence-run-root", roots.evidenceRunRoot,
    "--qualified-run-root", roots.qualifiedRunRoot,
    "--source-run-id", source().runId,
  ], temporaryRoot), { ...roots, sourceRunId: source().runId, temporaryRoot });
  assert.throws(() => parseR16CreatorQualificationArguments([
    "--prototype-run-root", roots.prototypeRunRoot,
    "--spatial-run-root", roots.spatialRunRoot,
    "--solved-run-root", roots.solvedRunRoot,
    "--evidence-run-root", roots.evidenceRunRoot,
    "--run-id", source().runId,
  ], temporaryRoot), { code: "R16_CREATOR_QUALIFICATION_ARGUMENT_INVALID" });
  assert.throws(() => parseR16CreatorQualificationArguments([
    "--prototype-run-root", roots.prototypeRunRoot,
    "--spatial-run-root", roots.spatialRunRoot,
    "--solved-run-root", roots.solvedRunRoot,
    "--evidence-run-root", roots.evidenceRunRoot,
    "--qualified-run-root", roots.qualifiedRunRoot,
    "--source-run-id", "not-a-source-run",
  ], temporaryRoot), { code: "R16_CREATOR_QUALIFICATION_ARGUMENT_INVALID" });
  assert.throws(() => parseR16CreatorQualificationArguments([
    "--prototype-run-root", roots.prototypeRunRoot,
    "--spatial-run-root", roots.spatialRunRoot,
    "--solved-run-root", roots.solvedRunRoot,
    "--evidence-run-root", roots.evidenceRunRoot,
    "--qualified-run-root", path.join(temporaryRoot, "nested", "qualified"),
  ], temporaryRoot), { code: "R16_CREATOR_QUALIFICATION_ARGUMENT_INVALID" });
});

test("source-only resumes through all local R13-R15 stages", async () => {
  const { calls, result, stages } = await runLevel("source-only");
  assert.equal(result.ok, true);
  assert.deepEqual(calls, ["analyze", "solve", "verify", "collectEvidence", "publishQualification"]);
  assert.deepEqual(stages.map((item) => `${item.subphase}:${item.attempt}`), [
    "analyzing:0", "solving:0", "verifying:0", "evidencing:0", "evidencing:1",
  ]);
});

test("solved-only resumes at verification and always collects fresh evidence", async () => {
  const { calls, result } = await runLevel("solved-only");
  assert.equal(result.ok, true);
  assert.deepEqual(calls, ["verifySolved", "collectEvidence", "publishQualification"]);
});

test("evidence-only verifies evidence then publishes without Godot collection", async () => {
  const { calls, result } = await runLevel("evidence-only");
  assert.equal(result.ok, true);
  assert.deepEqual(calls, ["verifyEvidence", "publishQualification"]);
});

test("qualified resumes with reference verification only", async () => {
  const { calls, result, stages } = await runLevel("qualified");
  assert.equal(result.ok, true);
  assert.equal(result.reusedQualification, true);
  assert.deepEqual(calls, ["verifyQualified"]);
  assert.deepEqual(stages.map((item) => item.subphase), ["verifying"]);
});

test("R15 may publish a newly evidenced final solution when no solution was pinned", async () => {
  const { calls, result } = await runLevel("solved-only", {
    initialSolution: hash("4"),
    finalSolution: hash("6"),
  });
  assert.equal(result.ok, true);
  assert.equal(result.qualification.hashes.spatialSolutionSha256, hash("6"));
  assert.deepEqual(calls, ["verifySolved", "collectEvidence", "publishQualification"]);
});

test("an explicitly pinned different solution forces rejection and never publishes", async () => {
  const { calls, result } = await runLevel("solved-only", {
    initialSolution: hash("4"),
    finalSolution: hash("6"),
  }, { expectedSolutionSha256: hash("4") });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_FAILED");
  assert.deepEqual(calls, ["verifySolved", "collectEvidence"]);
});

test("failed evidence leaves the previous qualified current untouched", async () => {
  let current = "previous-qualified-run";
  const calls = [];
  const operations = operationsFor("solved-only", calls, { failAt: "collectEvidence" });
  const result = await qualifyR16Creator(baseOptions, {
    discoverCache: async () => discovered("solved-only"),
    operations: {
      ...operations,
      publishQualification: async () => {
        current = "unexpected-new-current";
        return { ok: true, qualification: qualification() };
      },
    },
  });
  assert.equal(result.ok, false);
  assert.equal(current, "previous-qualified-run");
  assert.deepEqual(calls, ["verifySolved", "collectEvidence"]);
});

test("cache-only qualification does not need network or credential capabilities", async () => {
  for (const level of ["qualified", "evidence-only", "solved-only", "source-only"]) {
    const { result } = await runLevel(level);
    assert.equal(result.ok, true, level);
  }
});

test("default adapter delegates solving and evidence to the frozen R14 and R15 qualifiers", async () => {
  const calls = [];
  const solutionJson = canonicalizeJsonValue({ solution: "candidate" });
  const verificationJson = canonicalizeJsonValue({ verification: "passed" });
  const solutionSha256 = `sha256:${createHash("sha256")
    .update(solutionJson).digest("hex")}`;
  const verificationSha256 = `sha256:${createHash("sha256")
    .update(verificationJson).digest("hex")}`;
  const identity = {
    runtimePackSha256: hash("1"), runtimeReceiptSha256: hash("2"),
    environmentFactsSha256: hash("3"), spatialIntentSha256: hash("4"),
    assetBundleSha256: hash("5"), spatialSolutionSha256: solutionSha256,
    spatialVerificationSha256: verificationSha256,
  };
  const replayPlanJson = canonicalizeJsonValue({ identity });
  const replayPlanSha256 = `sha256:${createHash("sha256")
    .update(replayPlanJson).digest("hex")}`;
  const canonicalEvidenceJson = canonicalizeJsonValue({
    status: "passed", replayPlanSha256, identity, attempt: 2,
    observations: [{}], performance: { sampleCount: 300, medianFrameMicros: 16667, medianFpsMilli: 60000 },
    media: { screenshots: [{}], videos: [{}] },
  });
  const loadedEvidence = {
    runId: "7".repeat(64), replayPlanJson, canonicalEvidenceJson,
    previewFiles: new Map([
      ["spatial-solution.json", new TextEncoder().encode(solutionJson)],
      ["spatial-verification-report.json", new TextEncoder().encode(verificationJson)],
    ]),
    mediaFiles: new Map(),
  };
  const dependencies = {
    qualifyR14SpatialSolver: async ({ runId }) => {
      calls.push(`r14:${runId}`);
      return { ok: true, runId, solutionSha256 };
    },
    loadVerifiedSolvedSpatialPrototypeRun: async ({ runId }) => ({
      runId, promptSha256: source().promptSha256, model: source().model,
      solutionSha256, previewFiles: new Map(),
    }),
    qualifyR15RuntimeEvidence: async ({ runId }) => {
      calls.push(`r15:${runId}`);
      return { ok: true, attempt: 2, published: { runId: loadedEvidence.runId } };
    },
    loadVerifiedRuntimeEvidenceRun: async ({ runId }) => {
      assert.equal(runId, loadedEvidence.runId);
      return loadedEvidence;
    },
    canonicalizeJsonValue,
    services: {},
  };
  const cache = {
    cacheLevel: "source-only", source: source(), verifyReferences: async () => true,
  };
  const operations = createR16CreatorQualificationOperations(baseOptions, cache, dependencies);
  const solved = await operations.solve({});
  assert.equal(solved.solved.solutionSha256, solutionSha256);
  const attempts = [];
  const evidence = await operations.collectEvidence({
    initialSolutionSha256: solutionSha256,
    expectedSolutionSha256: undefined,
    onAttempt: (attempt) => attempts.push(attempt),
  });
  assert.equal(evidence.finalSolutionSha256, solutionSha256);
  assert.deepEqual(attempts, [1, 2]);
  assert.deepEqual(calls, [`r14:${source().runId}`, `r15:${source().runId}`]);

  const zeroEvidence = {
    ...loadedEvidence,
    canonicalEvidenceJson: canonicalizeJsonValue({
      ...JSON.parse(canonicalEvidenceJson),
      attempt: 0,
    }),
  };
  const zeroOperations = createR16CreatorQualificationOperations(baseOptions, cache, {
    ...dependencies,
    qualifyR15RuntimeEvidence: async () => ({
      ok: true,
      attempt: 0,
      published: { runId: zeroEvidence.runId },
    }),
    loadVerifiedRuntimeEvidenceRun: async () => zeroEvidence,
  });
  const zeroAttempts = [];
  const zeroResult = await zeroOperations.collectEvidence({
    initialSolutionSha256: solutionSha256,
    expectedSolutionSha256: undefined,
    onAttempt: (attempt) => zeroAttempts.push(attempt),
  });
  assert.equal(zeroResult.attempt, 0);
  assert.deepEqual(zeroAttempts, []);
});

test("strong source or asset reference drift blocks qualification current publication", async () => {
  let published = false;
  const candidate = {
    cacheLevel: "evidence-only",
    source: source(),
    verifyReferences: async () => false,
  };
  const operations = createR16CreatorQualificationOperations(baseOptions, candidate, {
    buildCreatorQualificationReferences: async (request) => {
      assert.equal(request.sourceRunId, source().runId);
      assert.equal(request.evidenceRunId, "8".repeat(64));
      return {
        ok: false,
        valid: false,
        diagnostics: [{ code: "R16_CREATOR_QUALIFICATION_REFERENCE_INVALID" }],
      };
    },
    publishQualifiedCreatorRun: async () => {
      published = true;
      return { qualificationRunId: "9".repeat(64) };
    },
  });
  const result = await operations.publishQualification({
    source: source(),
    evidence: { runId: "8".repeat(64), solutionSha256: hash("4") },
    expectedSolutionSha256: hash("4"),
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "R16_CREATOR_QUALIFICATION_REFERENCE_INVALID");
  assert.equal(published, false);
});

test("same prompt and model cannot reuse a qualification from a different source run", async () => {
  const selectedSource = source();
  const differentSourceRunId = `${"a".repeat(64)}-${"b".repeat(64)}`;
  const result = await discoverR16CreatorQualificationCache(baseOptions, {
    recoverPrototypeRuns: async () => ({
      currentRunId: selectedSource.runId,
      runs: [selectedSource],
    }),
    createCreatorQualificationReferenceVerifier: () => async () => ({ valid: true }),
    findVerifiedQualifiedCreatorRun: async () => ({
      qualificationRunId: "c".repeat(64),
      qualification: {
        sourceRunId: differentSourceRunId,
        promptSha256: selectedSource.promptSha256,
        model: selectedSource.model,
        hashes: { spatialSolutionSha256: hash("4") },
      },
    }),
    recoverRuntimeEvidenceRuns: async () => ({ currentRunId: null, runs: [] }),
    loadVerifiedSolvedSpatialPrototypeRun: async () => {
      throw new Error("no solved cache");
    },
    services: {},
  });
  assert.equal(result.cacheLevel, "source-only");
  assert.equal(result.source.runId, selectedSource.runId);
});

test("CLI resolves Godot 4.6.3 and forwards stage evidence without a source id argument", async () => {
  const args = [
    "--prototype-run-root", roots.prototypeRunRoot,
    "--spatial-run-root", roots.spatialRunRoot,
    "--solved-run-root", roots.solvedRunRoot,
    "--evidence-run-root", roots.evidenceRunRoot,
    "--qualified-run-root", roots.qualifiedRunRoot,
  ];
  const observed = [];
  const output = await runR16CreatorQualificationCli(args, {
    temporaryRoot,
    resolveGodotBinary: () => ({ command: path.join(temporaryRoot, "tools", "godot.exe"), version: "4.6.3" }),
    qualifyR16Creator: async (options) => {
      assert.equal(options.sourceRunId, undefined);
      assert.equal(options.godotVersion, "4.6.3");
      await options.onStage({ stage: "qualifying", subphase: "verifying", attempt: 0 });
      return { ok: true, cacheLevel: "qualified", reusedQualification: true, qualification: qualification() };
    },
    onStage: (stage) => observed.push(stage),
  });
  assert.equal(output.result.ok, true);
  assert.deepEqual(output.stages, observed);
  await assert.rejects(() => runR16CreatorQualificationCli(args, {
    temporaryRoot,
    resolveGodotBinary: () => ({ command: path.join(temporaryRoot, "tools", "godot.exe"), version: "4.6.2" }),
  }), { code: "GODOT_4_6_3_NOT_AVAILABLE" });
});
