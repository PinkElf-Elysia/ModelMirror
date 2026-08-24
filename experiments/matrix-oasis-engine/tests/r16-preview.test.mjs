import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createR16PreviewOperations,
  parseR16PreviewArguments,
  R16_PREVIEW_READY_MARKER,
  selectR16QualifiedEvidence,
} from "../scripts/lib/r16-preview-core.mjs";
import { parseR16CaptureArguments } from "../scripts/capture-r16.mjs";

const temporaryRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const runRoot = path.join(temporaryRoot, "matrix-oasis-r16-preview-test");
const sourceRunId = `${"a".repeat(64)}-${"b".repeat(64)}`;
const evidenceRunId = "c".repeat(64);
const solutionSha256 = `sha256:${"d".repeat(64)}`;
const promptSha256 = `sha256:${"e".repeat(64)}`;

function baseOperations(overrides = {}) {
  const value = {
    findCache: async () => ({ ok: true, runId: sourceRunId }),
    generate: async () => ({ ok: true }),
    describeAssets: async () => ({ ok: true }),
    acquire: async () => ({ ok: true }),
    publish: async () => ({ ok: true, runId: sourceRunId }),
    launch: async () => ({ ok: true }),
    recover: async () => ({ currentRunId: sourceRunId,
      runs: [{ runId: sourceRunId, promptSha256, model: "luna" }] }),
    stopLaunch: async () => {},
    persistPending: async () => {},
    recoverPending: async () => ({ runs: [] }),
    discardPending: async () => {},
    ...overrides,
  };
  return value;
}

function options(r12Operations = baseOperations()) {
  return {
    ...parseR16PreviewArguments(["--run-root", runRoot], temporaryRoot),
    godot: { command: path.join(temporaryRoot, "godot.exe"), version: "4.6.3" },
    moduleRoot: process.cwd(),
    r12Operations,
  };
}

function qualification() {
  return {
    format: "matrix-oasis.prototype-creator-qualification",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    profile: "matrix-oasis.creator-solved-evidence/1",
    status: "qualified",
    promptSha256,
    model: "luna",
    sourceRunId,
    hashes: { spatialSolutionSha256: solutionSha256 },
    evidence: { runId: evidenceRunId },
  };
}

test("R16 preview arguments derive five isolated direct-child cache roots", () => {
  const parsed = parseR16PreviewArguments(["--run-root", runRoot, "--port", "43116"], temporaryRoot);
  assert.equal(parsed.prototypeRunRoot, runRoot);
  assert.equal(parsed.spatialRunRoot, `${runRoot}-spatial`);
  assert.equal(parsed.solvedRunRoot, `${runRoot}-solved`);
  assert.equal(parsed.evidenceRunRoot, `${runRoot}-evidence`);
  assert.equal(parsed.qualifiedRunRoot, `${runRoot}-qualified`);
  assert.equal(parsed.port, 43116);
  assert.equal(R16_PREVIEW_READY_MARKER, "MATRIX_OASIS_R16_CREATOR_MVP_READY");
  for (const invalid of [[], ["--run-root", "relative"], ["--run-root", `${runRoot}-solved`],
    ["--run-root", runRoot, "--port", "80"]]) {
    assert.throws(() => parseR16PreviewArguments(invalid, temporaryRoot), /R16_PREVIEW_ARGUMENT_INVALID/u);
  }
});

test("R16 capture accepts only one qualified root and a new direct-child output", async () => {
  const output = path.join(temporaryRoot, "matrix-oasis-r16-capture-test");
  assert.deepEqual(parseR16CaptureArguments(["--qualified-run-root", `${runRoot}-qualified`, "--output", output], temporaryRoot),
    { qualifiedRunRoot: `${runRoot}-qualified`, output });
  assert.throws(() => parseR16CaptureArguments(["--qualified-run-root", runRoot, "--output", output], temporaryRoot),
    /R16_CAPTURE_ARGUMENT_INVALID/u);
  const qualified = qualification();
  const selected = await selectR16QualifiedEvidence({ qualifiedRunRoot: `${runRoot}-qualified`, temporaryRoot }, {
    createR16QualificationReferenceVerifier: () => async () => true,
    recoverQualifiedCreatorRuns: async () => ({ currentQualificationRunId: "f".repeat(64),
      runs: [{ qualificationRunId: "f".repeat(64), qualification: qualified }] }),
    selectR15EvidenceRun: async ({ runId }) => ({ runId, solutionSha256 }),
  });
  assert.equal(selected.qualificationRunId, "f".repeat(64));
  assert.equal(selected.evidence.runId, evidenceRunId);
});

test("R16 preview maps source cache levels and adds the canonical qualification id", async () => {
  const discovered = { cacheLevel: "source-only", source: { runId: sourceRunId }, expectedSolutionSha256: undefined };
  const qualified = qualification();
  const operations = createR16PreviewOperations(options(baseOperations({
    findCache: async () => { throw new Error("R12_TOPIC_PROFILE_MUST_NOT_FILTER_GENERIC_SOURCE"); },
  })), {
    createR16QualificationReferenceVerifier: () => async () => true,
    recoverR16CreatorSourceRuns: async () => ({ currentRunId: sourceRunId,
      runs: [{ runId: sourceRunId, promptSha256, model: "luna" }] }),
    discoverR16CreatorQualificationCache: async (request) => {
      assert.equal(request.sourceRunId, sourceRunId);
      return discovered;
    },
    qualifyR16Creator: async (request) => {
      await request.onStage({ stage: "qualifying", subphase: "analyzing", attempt: 0 });
      return { ok: true, cacheLevel: "source-only", reusedQualification: false, qualification: qualified };
    },
  });
  assert.deepEqual(await operations.findCache({ promptSha256, model: "luna", prompt: "neutral" }), {
    ok: true, cacheLevel: "source-only", sourceRunId, expectedSolutionSha256: null,
  });
  const stages = [];
  const result = await operations.qualify({ sourceRunId, expectedSolutionSha256: null,
    onStage: async (stage) => stages.push(stage) });
  const expected = createHash("sha256").update(canonicalizeJsonValue(qualified), "utf8").digest("hex");
  assert.equal(result.qualificationRunId, expected);
  assert.deepEqual(stages, [{ stage: "qualifying", subphase: "analyzing", attempt: 0 }]);
});

test("R16 launch revalidates qualification and selects its exact R15 evidence preview", async () => {
  const qualified = qualification();
  let selectedId = null;
  let cleanupCount = 0;
  const operations = createR16PreviewOperations(options(), {
    createR16QualificationReferenceVerifier: () => async () => true,
    loadVerifiedQualifiedCreatorRun: async ({ qualificationRunId }) => {
      assert.equal(qualificationRunId, "f".repeat(64));
      return { qualification: qualified };
    },
    selectR15EvidenceRun: async ({ runId }) => {
      selectedId = runId;
      return { runId, solutionSha256 };
    },
    launchR15EvidencePreview: async ({ selected }) => ({ child: {}, project: {},
      cleanup: async () => { assert.equal(selected.runId, evidenceRunId); cleanupCount += 1; } }),
  });
  assert.deepEqual(await operations.launch({ runId: "f".repeat(64) }), { ok: true });
  assert.equal(selectedId, evidenceRunId);
  await operations.stopLaunch();
  assert.equal(cleanupCount, 1);
});

test("R16 recovery exposes only strong qualifications and at most one partial cache", async () => {
  const qualified = qualification();
  const qualificationRunId = "f".repeat(64);
  const operations = createR16PreviewOperations(options(), {
    createR16QualificationReferenceVerifier: () => async () => true,
    recoverQualifiedCreatorRuns: async () => ({ currentQualificationRunId: qualificationRunId,
      runs: [{ qualificationRunId, qualification: qualified }] }),
    recoverR16CreatorSourceRuns: async () => ({ currentRunId: sourceRunId,
      runs: [{ runId: sourceRunId, promptSha256, model: "luna" }] }),
  });
  const recovered = await operations.recover();
  assert.equal(recovered.currentRunId, qualificationRunId);
  assert.equal(recovered.runs.length, 1);
  assert.equal(recovered.runs[0].cache.cacheLevel, "qualified");
});
