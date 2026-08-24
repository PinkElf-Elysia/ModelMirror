import test from "node:test";
import assert from "node:assert/strict";
import { qualifyPrototypeForCreator } from "../src/index.mjs";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const solutionHash = `sha256:${"5".repeat(64)}`;
const otherSolutionHash = `sha256:${"6".repeat(64)}`;

const solved = (hash = solutionHash) => ({
  kind: "solved",
  hashes: { spatialSolutionSha256: hash },
});

const verification = (hash = solutionHash) => ({
  kind: "verification",
  hashes: { spatialSolutionSha256: hash },
});

const evidence = (hash = solutionHash, attempt = 0) => ({
  kind: "evidence",
  attempt,
  identity: { spatialSolutionSha256: hash },
});

const canonicalEvidence = (hash = solutionHash, attempt = 0) => ({
  canonicalEvidenceJson: canonicalizeJsonValue({
    format: "matrix-oasis.prototype-runtime-evidence",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    replayPlanSha256: `sha256:${"7".repeat(64)}`,
    identity: {
      runtimePackSha256: `sha256:${"0".repeat(64)}`,
      runtimeReceiptSha256: `sha256:${"1".repeat(64)}`,
      environmentFactsSha256: `sha256:${"2".repeat(64)}`,
      spatialIntentSha256: `sha256:${"3".repeat(64)}`,
      assetBundleSha256: `sha256:${"4".repeat(64)}`,
      spatialSolutionSha256: hash,
      spatialVerificationSha256: `sha256:${"8".repeat(64)}`,
    },
    attempt,
    status: "passed",
    observations: [],
    performance: { sampleCount: 300, medianFrameMicros: 16667, medianFpsMilli: 59998 },
    media: { screenshots: [], videos: [] },
    repairs: [],
  }),
});

const qualification = (hash = solutionHash, attempt = 0) => ({
  kind: "qualification",
  hashes: { spatialSolutionSha256: hash },
  evidence: { attempt },
});

function harness(overrides = {}) {
  const calls = [];
  const operations = {
    async verifyQualified(input) {
      calls.push(["verifyQualified", input]);
      return { valid: true };
    },
    async verifyEvidence(input) {
      calls.push(["verifyEvidence", input]);
      return { valid: true };
    },
    async verifySolved(input) {
      calls.push(["verifySolved", input]);
      return { valid: true };
    },
    async analyze(input) {
      calls.push(["analyze", input]);
      return { ok: true, analysis: { kind: "analysis" } };
    },
    async solve(input) {
      calls.push(["solve", input]);
      return { ok: true, solved: solved() };
    },
    async verify(input) {
      calls.push(["verify", input]);
      return { ok: true, verification: verification() };
    },
    async collectEvidence(input) {
      calls.push(["collectEvidence", input]);
      await input.onAttempt(1);
      await input.onAttempt(2);
      return {
        ok: true,
        attempt: 2,
        solved: input.solved,
        verification: input.verification,
        evidence: evidence(solutionHash, 2),
      };
    },
    async publishQualification(input) {
      calls.push(["publishQualification", input]);
      return { ok: true, qualification: qualification(input.expectedSolutionSha256, input.evidence?.attempt ?? 0) };
    },
    ...overrides,
  };
  return { calls, operations };
}

function names(calls) {
  return calls.map(([name]) => name);
}

test("qualified cache only revalidates references", async () => {
  const { calls, operations } = harness();
  const stages = [];
  const candidate = qualification(solutionHash, 1);
  const result = await qualifyPrototypeForCreator({
    cacheLevel: "qualified",
    qualified: candidate,
    expectedSolutionSha256: solutionHash,
    onStage: (stage) => stages.push(stage),
  }, operations);
  assert.equal(result.ok, true);
  assert.equal(result.reusedQualification, true);
  assert.deepEqual(names(calls), ["verifyQualified"]);
  assert.deepEqual(stages, [{ stage: "qualifying", subphase: "verifying", attempt: 1 }]);
});

test("evidence-only verifies and publishes without collecting evidence", async () => {
  const { calls, operations } = harness();
  const result = await qualifyPrototypeForCreator({
    cacheLevel: "evidence-only",
    source: { id: "source" },
    solved: solved(),
    verification: verification(),
    evidence: evidence(solutionHash, 1),
  }, operations);
  assert.equal(result.ok, true);
  assert.equal(result.reusedQualification, false);
  assert.deepEqual(names(calls), ["verifyEvidence", "publishQualification"]);
});

test("evidence-only strictly accepts the canonical R15 cache wrapper", async () => {
  const { calls, operations } = harness();
  const result = await qualifyPrototypeForCreator({
    cacheLevel: "evidence-only",
    evidence: canonicalEvidence(solutionHash, 1),
  }, operations);
  assert.equal(result.ok, true);
  assert.deepEqual(names(calls), ["verifyEvidence", "publishQualification"]);

  const malformed = canonicalEvidence();
  malformed.canonicalEvidenceJson = `${malformed.canonicalEvidenceJson} `;
  const rejected = await qualifyPrototypeForCreator({ cacheLevel: "evidence-only", evidence: malformed }, operations);
  assert.equal(rejected.ok, false);
  assert.equal(rejected.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
});

test("solved-only runs evidence but does not analyze, solve, or rerun Godot verification", async () => {
  const { calls, operations } = harness();
  const stages = [];
  const result = await qualifyPrototypeForCreator({
    cacheLevel: "solved-only",
    source: { id: "source" },
    solved: solved(),
    verification: verification(),
    onStage: (stage) => stages.push(stage),
  }, operations);
  assert.equal(result.ok, true);
  assert.deepEqual(names(calls), ["verifySolved", "collectEvidence", "publishQualification"]);
  assert.deepEqual(stages, [
    { stage: "qualifying", subphase: "verifying", attempt: 0 },
    { stage: "qualifying", subphase: "evidencing", attempt: 0 },
    { stage: "qualifying", subphase: "evidencing", attempt: 1 },
    { stage: "qualifying", subphase: "evidencing", attempt: 2 },
  ]);
});

test("source-only executes the complete local pipeline in stage order", async () => {
  const { calls, operations } = harness();
  const stages = [];
  const source = { id: "source", nested: { stable: true } };
  const result = await qualifyPrototypeForCreator({
    cacheLevel: "source-only",
    source,
    onStage: (stage) => stages.push(stage),
  }, operations);
  assert.equal(result.ok, true);
  assert.deepEqual(names(calls), ["analyze", "solve", "verify", "collectEvidence", "publishQualification"]);
  assert.deepEqual(stages.map(({ subphase, attempt }) => `${subphase}:${attempt}`), [
    "analyzing:0",
    "solving:0",
    "verifying:0",
    "evidencing:0",
    "evidencing:1",
    "evidencing:2",
  ]);
  assert.equal(Object.isFrozen(source), false);
  assert.deepEqual(source, { id: "source", nested: { stable: true } });
  assert.equal(Object.isFrozen(calls[0][1]), true);
  assert.equal(Object.isFrozen(calls[0][1].source.nested), true);
});

test("expected solution identity blocks old evidence and different collected evidence", async () => {
  {
    const { calls, operations } = harness();
    const result = await qualifyPrototypeForCreator({
      cacheLevel: "evidence-only",
      expectedSolutionSha256: solutionHash,
      evidence: evidence(otherSolutionHash, 0),
    }, operations);
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
    assert.deepEqual(calls, []);
  }
  {
    const { calls, operations } = harness({
      async collectEvidence(input) {
        calls.push(["collectEvidence", input]);
        return {
          ok: true,
          attempt: 0,
          solved: solved(otherSolutionHash),
          verification: verification(otherSolutionHash),
          evidence: evidence(otherSolutionHash, 0),
        };
      },
    });
    const result = await qualifyPrototypeForCreator({
      cacheLevel: "solved-only",
      expectedSolutionSha256: solutionHash,
      solved: solved(),
      verification: verification(),
    }, operations);
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_FAILED");
    assert.deepEqual(names(calls), ["verifySolved", "collectEvidence"]);
  }
});

test("R15 repair may publish a different final solution unless an explicit identity is locked", async () => {
  const repairedOperations = (calls) => harness({
    async collectEvidence(input) {
      calls.push(["collectEvidence", input]);
      await input.onAttempt(1);
      return {
        ok: true,
        attempt: 1,
        finalSolutionSha256: otherSolutionHash,
        solved: solved(otherSolutionHash),
        verification: verification(otherSolutionHash),
        evidence: evidence(otherSolutionHash, 1),
      };
    },
  }).operations;

  {
    const calls = [];
    const operations = repairedOperations(calls);
    const result = await qualifyPrototypeForCreator({
      cacheLevel: "solved-only",
      solved: solved(),
      verification: verification(),
    }, operations);
    assert.equal(result.ok, true);
    assert.equal(result.qualification.hashes.spatialSolutionSha256, otherSolutionHash);
    assert.deepEqual(names(calls), ["collectEvidence"]);
  }
  {
    const calls = [];
    const operations = repairedOperations(calls);
    const result = await qualifyPrototypeForCreator({
      cacheLevel: "source-only",
      source: { id: "source" },
    }, operations);
    assert.equal(result.ok, true);
    assert.equal(result.qualification.hashes.spatialSolutionSha256, otherSolutionHash);
  }
  {
    const calls = [];
    const operations = repairedOperations(calls);
    const result = await qualifyPrototypeForCreator({
      cacheLevel: "solved-only",
      expectedSolutionSha256: solutionHash,
      solved: solved(),
      verification: verification(),
    }, operations);
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_FAILED");
    assert.equal(names(calls).includes("publishQualification"), false);
  }
});

test("a failed prerequisite or thrown operation never publishes", async () => {
  {
    const { calls, operations } = harness({
      async analyze(input) {
        calls.push(["analyze", input]);
        return { ok: false };
      },
    });
    const result = await qualifyPrototypeForCreator({ cacheLevel: "source-only", source: {} }, operations);
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_ANALYSIS_FAILED");
    assert.deepEqual(names(calls), ["analyze"]);
  }
  {
    const { calls, operations } = harness({
      async collectEvidence(input) {
        calls.push(["collectEvidence", input]);
        throw new Error("private path or provider body");
      },
    });
    const result = await qualifyPrototypeForCreator({ cacheLevel: "solved-only", solved: solved() }, operations);
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_INTERNAL_ERROR");
    assert.equal(JSON.stringify(result).includes("private path"), false);
    assert.deepEqual(names(calls), ["verifySolved", "collectEvidence"]);
  }
});

test("missing conditional operations and out-of-range attempts fail closed", async () => {
  assert.equal((await qualifyPrototypeForCreator({ cacheLevel: "source-only", source: {} }, {})).diagnostics[0].code,
    "PROTOTYPE_CREATOR_QUALIFICATION_INPUT_INVALID");
  const { calls, operations } = harness({
    async collectEvidence(input) {
      calls.push(["collectEvidence", input]);
      await input.onAttempt(3);
      return { ok: true, attempt: 3, evidence: evidence() };
    },
  });
  const result = await qualifyPrototypeForCreator({ cacheLevel: "solved-only", solved: solved() }, operations);
  assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_INTERNAL_ERROR");
  assert.deepEqual(names(calls), ["verifySolved", "collectEvidence"]);
});

test("a final attempt without callbacks is expanded continuously by the orchestrator", async () => {
  const { calls, operations } = harness({
    async collectEvidence(input) {
      calls.push(["collectEvidence", input]);
      return {
        ok: true,
        attempt: 2,
        solved: input.solved,
        verification: input.verification,
        evidence: evidence(solutionHash, 2),
      };
    },
  });
  const stages = [];
  const result = await qualifyPrototypeForCreator({
    cacheLevel: "solved-only",
    solved: solved(),
    verification: verification(),
    onStage: (stage) => stages.push(stage),
  }, operations);
  assert.equal(result.ok, true);
  assert.deepEqual(stages.map((item) => item.attempt), [0, 0, 1, 2]);
});

test("operation-reported attempt jumps, reversals, and final regressions fail closed", async (t) => {
  const execute = async (collectEvidence) => {
    const { calls, operations } = harness({
      async collectEvidence(input) {
        calls.push(["collectEvidence", input]);
        return await collectEvidence(input);
      },
    });
    const result = await qualifyPrototypeForCreator({
      cacheLevel: "solved-only",
      solved: solved(),
      verification: verification(),
    }, operations);
    assert.equal(names(calls).includes("publishQualification"), false);
    return result;
  };
  await t.test("jump", async () => {
    const result = await execute(async (input) => {
      await input.onAttempt(2);
      return { ok: true, attempt: 2, solved: solved(), verification: verification(), evidence: evidence(solutionHash, 2) };
    });
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_INTERNAL_ERROR");
  });
  await t.test("reverse", async () => {
    const result = await execute(async (input) => {
      await input.onAttempt(1);
      await input.onAttempt(0);
      return { ok: true, attempt: 1, solved: solved(), verification: verification(), evidence: evidence(solutionHash, 1) };
    });
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_INTERNAL_ERROR");
  });
  await t.test("final lower than highest callback", async () => {
    const result = await execute(async (input) => {
      await input.onAttempt(1);
      await input.onAttempt(2);
      return { ok: true, attempt: 1, solved: solved(), verification: verification(), evidence: evidence(solutionHash, 1) };
    });
    assert.equal(result.diagnostics[0].code, "PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_FAILED");
  });
});

test("twenty identical source-only runs return byte-identical results", async () => {
  let expected = null;
  for (let index = 0; index < 20; index += 1) {
    const { operations } = harness();
    const result = await qualifyPrototypeForCreator({ cacheLevel: "source-only", source: { id: "source" } }, operations);
    const bytes = JSON.stringify(result);
    expected ??= bytes;
    assert.equal(bytes, expected);
    assert.equal(Object.isFrozen(result), true);
    assert.equal(Object.isFrozen(result.qualification.hashes), true);
  }
});
