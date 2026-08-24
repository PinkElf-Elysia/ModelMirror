import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  buildCreatorQualificationReferences,
  createCreatorQualificationReferenceVerifier,
  verifyCreatorQualificationReferences,
} from "../scripts/lib/creator-qualification-cache-core.mjs";

const SOURCE_RUN_ID = `${"a".repeat(64)}-${"b".repeat(64)}`;
const PROMPT_SHA256 = `sha256:${"c".repeat(64)}`;
const MODEL = "openai/gpt-5.6-luna";
const temporaryRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const encode = (text) => new TextEncoder().encode(text);
const sha256 = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const canonicalBytes = (value) => encode(canonicalizeJsonValue(value));

function runtimeFixture() {
  const prop = Uint8Array.of(1, 3, 3, 7);
  const splat = Uint8Array.of(2, 4, 6, 8);
  const collider = Uint8Array.of(9, 8, 7, 6);
  const assetBundle = {
    materializations: [{
      assetBriefId: "asset-prop",
      assets: [{
        id: "asset-prop-visual",
        path: "assets/asset-prop.glb",
        byteLength: prop.byteLength,
        sha256: sha256(prop),
      }],
    }],
  };
  const scenePack = {
    assets: [{
      id: "asset-prop-visual",
      path: "assets/asset-prop.glb",
      byteLength: prop.byteLength,
      sha256: sha256(prop).slice(7),
    }],
  };
  const previewFiles = new Map([
    ["runtime-game-pack.json", canonicalBytes({ source: { id: "prototype", contentVersion: "1" } })],
    ["runtime-receipt.json", canonicalBytes({ artifact: { sha256: "d".repeat(64) } })],
    ["environment-facts.json", canonicalBytes({ facts: true })],
    ["spatial-intent.json", canonicalBytes({ intent: true })],
    ["prototype-asset-bundle.json", canonicalBytes(assetBundle)],
    ["scene-pack.json", canonicalBytes(scenePack)],
    ["assets/asset-prop.glb", prop],
    ["assets/environment.compressed.ply", splat],
    ["assets/environment-collider.glb", collider],
  ]);
  const assembly = {
    sources: { scenePackSha256: sha256(previewFiles.get("scene-pack.json")) },
    environment: {
      splat: { path: "assets/environment.compressed.ply", sha256: sha256(splat) },
      collider: { path: "assets/environment-collider.glb", sha256: sha256(collider) },
    },
  };
  previewFiles.set("spatial-assembly.json", canonicalBytes(assembly));
  const finalSolution = {
    source: {
      analysisTransformSource: {
        canonicalSha256: sha256(previewFiles.get("spatial-assembly.json")),
      },
    },
  };
  previewFiles.set("spatial-solution.json", canonicalBytes(finalSolution));
  previewFiles.set("spatial-verification-report.json", canonicalBytes({
    solutionSha256: sha256(previewFiles.get("spatial-solution.json")),
  }));

  const identity = {
    runtimePackSha256: sha256(previewFiles.get("runtime-game-pack.json")),
    runtimeReceiptSha256: sha256(previewFiles.get("runtime-receipt.json")),
    environmentFactsSha256: sha256(previewFiles.get("environment-facts.json")),
    spatialIntentSha256: sha256(previewFiles.get("spatial-intent.json")),
    assetBundleSha256: sha256(previewFiles.get("prototype-asset-bundle.json")),
    spatialSolutionSha256: sha256(previewFiles.get("spatial-solution.json")),
    spatialVerificationSha256: sha256(previewFiles.get("spatial-verification-report.json")),
  };
  const plan = {
    format: "matrix-oasis.prototype-runtime-replay-plan",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    identity,
    profile: {
      id: "matrix-oasis.runtime-replay/1",
      maxReplays: 32,
      maxActionsPerReplay: 256,
      maxSemanticStates: 100000,
    },
    coverage: {
      declaredEndingCount: 1,
      reachableEndingCount: 1,
      activeNodeCount: 1,
      coveredNodeCount: 1,
      loop: "not-applicable",
      disabledAction: "not-applicable",
    },
    replays: [{
      id: "replay-0001",
      kind: "ending",
      actionIds: ["finish"],
      probeActionId: null,
      targetId: "ending-done",
      resetAfter: false,
      expectedLocationIds: ["node-entry", "ending-done"],
    }],
  };
  const replayPlanJson = canonicalizeJsonValue(plan);
  const screenshot = Uint8Array.of(4, 3, 2, 1);
  const video = Uint8Array.of(5, 6, 7, 8);
  const evidence = {
    format: "matrix-oasis.prototype-runtime-evidence",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    replayPlanSha256: sha256(Buffer.from(replayPlanJson, "utf8")),
    identity,
    attempt: 1,
    status: "passed",
    observations: [{
      replayId: "replay-0001",
      kind: "ending",
      outcome: "passed",
      checkpoints: [{
        sequence: 0,
        locationKind: "node",
        locationId: "node-entry",
        stepCount: 0,
        actionId: null,
        playerPositionMm: [0, 900, 0],
        floorDistanceMm: 0,
        capsuleClear: true,
        navigationPathComplete: true,
        focusedActionId: null,
        interactionDistanceMm: null,
        visiblePlacementIds: [],
      }],
    }],
    performance: {
      sampleCount: 300,
      medianFrameMicros: 16667,
      medianFpsMilli: Math.floor(1_000_000_000 / 16667),
    },
    media: {
      screenshots: [{
        replayId: "replay-0001",
        locationId: "node-entry",
        width: 960,
        height: 540,
        sha256: sha256(screenshot),
      }],
      videos: [{ scope: "full-run", frameRate: 30, frameCount: 300, sha256: sha256(video) }],
    },
    repairs: [{
      round: 1,
      kind: "placement",
      candidateKeySha256: `sha256:${"e".repeat(64)}`,
      diagnosticCode: "R15_PLACEMENT_RUNTIME_INVALID",
    }],
  };
  const canonicalEvidenceJson = canonicalizeJsonValue(evidence);
  const evidenceRunId = sha256(Buffer.from(canonicalEvidenceJson, "utf8")).slice(7);
  const mediaFiles = new Map([
    ["media/replay-0000-checkpoint-0000.png", screenshot],
    ["media/full-run.ogv", video],
  ]);
  const sourcePreviewFiles = new Map([...previewFiles].map(([relative, value]) => [relative, value.slice()]));
  const loadedEvidence = {
    runId: evidenceRunId,
    replayPlanJson,
    canonicalEvidenceJson,
    previewFiles,
    mediaFiles,
  };
  return { evidenceRunId, loadedEvidence, sourcePreviewFiles, finalSolutionSha256: identity.spatialSolutionSha256 };
}

function harness(fixture = runtimeFixture(), overrides = {}) {
  const calls = { evidence: [], source: [], replan: 0, solved: 0 };
  const operations = {
    async loadEvidence(request) {
      calls.evidence.push(request);
      if (overrides.loadEvidence) return await overrides.loadEvidence(request, fixture);
      return fixture.loadedEvidence;
    },
    async loadSource(request) {
      calls.source.push(request);
      if (overrides.loadSource) return await overrides.loadSource(request, fixture);
      return {
        runId: SOURCE_RUN_ID,
        promptSha256: PROMPT_SHA256,
        model: MODEL,
        previewFiles: fixture.sourcePreviewFiles,
      };
    },
    async replan(request) {
      calls.replan += 1;
      if (overrides.replan) return await overrides.replan(request, fixture);
      return { ok: true, canonicalReplayPlanJson: fixture.loadedEvidence.replayPlanJson };
    },
    // Deliberately unusable: the final R15 repair may not equal solved-current.
    async loadSolved() { calls.solved += 1; throw new Error("must not read solved-current"); },
  };
  const request = {
    sourceRunId: SOURCE_RUN_ID,
    evidenceRunId: fixture.evidenceRunId,
    evidenceRunRoot: path.join(temporaryRoot, "evidence"),
    sourceRunRoot: path.join(temporaryRoot, "source"),
    temporaryRoot,
  };
  return { fixture, operations, request, calls };
}

test("builds the qualification solely from the exact final evidence preview", async () => {
  const setup = harness();
  const result = await buildCreatorQualificationReferences(setup.request, setup.operations);
  assert.equal(result.ok, true);
  assert.equal(result.qualification.hashes.spatialSolutionSha256, setup.fixture.finalSolutionSha256);
  assert.equal(result.qualification.evidence.runId, setup.fixture.evidenceRunId);
  assert.equal(result.qualification.evidence.attempt, 1);
  assert.deepEqual({
    replayCount: result.qualification.evidence.replayCount,
    screenshotCount: result.qualification.evidence.screenshotCount,
    videoCount: result.qualification.evidence.videoCount,
    sampleCount: result.qualification.evidence.sampleCount,
  }, { replayCount: 1, screenshotCount: 1, videoCount: 1, sampleCount: 300 });
  assert.equal(setup.calls.solved, 0);
  assert.equal(setup.calls.evidence[0].runId, setup.fixture.evidenceRunId);
  assert.equal(setup.calls.evidence[0].includeFiles, true);
});

test("created verifier closes source, plan, evidence, preview asset and media identities", async () => {
  const setup = harness();
  const built = await buildCreatorQualificationReferences(setup.request, setup.operations);
  const verifier = createCreatorQualificationReferenceVerifier({
    evidenceRunRoot: setup.request.evidenceRunRoot,
    sourceRunRoot: setup.request.sourceRunRoot,
    temporaryRoot: setup.request.temporaryRoot,
  }, setup.operations);
  const verified = await verifier({ qualification: built.qualification });
  assert.equal(verified.valid, true);
  assert.equal(setup.calls.solved, 0);
});

test("production verifier refuses a summary-only source loader", async () => {
  const setup = harness();
  const built = await buildCreatorQualificationReferences(setup.request, setup.operations);
  const verifier = createCreatorQualificationReferenceVerifier({
    evidenceRunRoot: setup.request.evidenceRunRoot,
    sourceRunRoot: setup.request.sourceRunRoot,
    temporaryRoot: setup.request.temporaryRoot,
    loadSource: async () => ({ runId: SOURCE_RUN_ID, promptSha256: PROMPT_SHA256, model: MODEL }),
  }, { loadEvidence: setup.operations.loadEvidence, replan: setup.operations.replan });
  assert.equal((await verifier({ qualification: built.qualification })).valid, false);
});

test("rejects qualification fields that point at a stale solved-current candidate", async () => {
  const setup = harness();
  const built = await buildCreatorQualificationReferences(setup.request, setup.operations);
  const qualification = structuredClone(built.qualification);
  qualification.hashes.spatialSolutionSha256 = `sha256:${"0".repeat(64)}`;
  const verified = await verifyCreatorQualificationReferences({ ...setup.request, qualification }, setup.operations);
  assert.equal(verified.valid, false);
  assert.equal(setup.calls.solved, 0);
});

test("rejects independently drifted plan, preview asset, media and source identity", async (t) => {
  await t.test("replanned bytes differ", async () => {
    const setup = harness(runtimeFixture(), { replan: async () => ({ ok: true, canonicalReplayPlanJson: "{}" }) });
    const result = await buildCreatorQualificationReferences(setup.request, setup.operations);
    assert.equal(result.valid, false);
  });
  await t.test("preview asset bytes differ", async () => {
    const fixture = runtimeFixture();
    fixture.loadedEvidence.previewFiles.set("assets/asset-prop.glb", Uint8Array.of(0));
    const setup = harness(fixture);
    const result = await buildCreatorQualificationReferences(setup.request, setup.operations);
    assert.equal(result.valid, false);
  });
  await t.test("media bytes differ", async () => {
    const fixture = runtimeFixture();
    fixture.loadedEvidence.mediaFiles.set("media/full-run.ogv", Uint8Array.of(0));
    const setup = harness(fixture);
    const result = await buildCreatorQualificationReferences(setup.request, setup.operations);
    assert.equal(result.valid, false);
  });
  await t.test("screenshot uses a different replay ordinal path", async () => {
    const fixture = runtimeFixture();
    const screenshot = fixture.loadedEvidence.mediaFiles.get("media/replay-0000-checkpoint-0000.png");
    fixture.loadedEvidence.mediaFiles.delete("media/replay-0000-checkpoint-0000.png");
    fixture.loadedEvidence.mediaFiles.set("media/replay-0001-checkpoint-0000.png", screenshot);
    const setup = harness(fixture);
    const result = await buildCreatorQualificationReferences(setup.request, setup.operations);
    assert.equal(result.valid, false);
  });
  await t.test("source prompt differs", async () => {
    const setup = harness(runtimeFixture(), {
      loadSource: async (request, fixture) => ({
        runId: SOURCE_RUN_ID,
        promptSha256: `sha256:${"f".repeat(64)}`,
        model: MODEL,
        previewFiles: fixture.sourcePreviewFiles,
      }),
    });
    const built = await buildCreatorQualificationReferences(setup.request, setup.operations);
    assert.equal(built.ok, true);
    const qualification = structuredClone(built.qualification);
    qualification.promptSha256 = PROMPT_SHA256;
    const result = await verifyCreatorQualificationReferences({ ...setup.request, qualification }, setup.operations);
    assert.equal(result.valid, false);
  });
  await t.test("strong source preview differs by one asset byte", async () => {
    const fixture = runtimeFixture();
    fixture.sourcePreviewFiles.set("assets/asset-prop.glb", Uint8Array.of(1, 3, 3, 8));
    const setup = harness(fixture);
    const result = await buildCreatorQualificationReferences(setup.request, setup.operations);
    assert.equal(result.valid, false);
  });
});

test("rejects evidence summary drift and failed replay observations", async () => {
  const setup = harness();
  const built = await buildCreatorQualificationReferences(setup.request, setup.operations);
  const changed = structuredClone(built.qualification);
  changed.evidence.screenshotCount += 1;
  assert.equal((await verifyCreatorQualificationReferences({ ...setup.request, qualification: changed }, setup.operations)).valid, false);

  const fixture = runtimeFixture();
  const evidence = JSON.parse(fixture.loadedEvidence.canonicalEvidenceJson);
  evidence.observations[0].outcome = "failed";
  fixture.loadedEvidence.canonicalEvidenceJson = canonicalizeJsonValue(evidence);
  fixture.loadedEvidence.runId = sha256(Buffer.from(fixture.loadedEvidence.canonicalEvidenceJson, "utf8")).slice(7);
  const failed = harness(fixture);
  failed.request.evidenceRunId = fixture.loadedEvidence.runId;
  assert.equal((await buildCreatorQualificationReferences(failed.request, failed.operations)).valid, false);
});

test("rejects evidence attempt/repair and FPS arithmetic inconsistencies", async (t) => {
  const mutateEvidence = (mutate) => {
    const fixture = runtimeFixture();
    const evidence = JSON.parse(fixture.loadedEvidence.canonicalEvidenceJson);
    mutate(evidence);
    fixture.loadedEvidence.canonicalEvidenceJson = canonicalizeJsonValue(evidence);
    fixture.loadedEvidence.runId = sha256(Buffer.from(fixture.loadedEvidence.canonicalEvidenceJson, "utf8")).slice(7);
    const setup = harness(fixture);
    setup.request.evidenceRunId = fixture.loadedEvidence.runId;
    return setup;
  };
  await t.test("attempt differs from repair count", async () => {
    const setup = mutateEvidence((evidence) => { evidence.attempt = 2; });
    assert.equal((await buildCreatorQualificationReferences(setup.request, setup.operations)).valid, false);
  });
  await t.test("reported FPS differs from frame time", async () => {
    const setup = mutateEvidence((evidence) => { evidence.performance.medianFpsMilli += 1; });
    assert.equal((await buildCreatorQualificationReferences(setup.request, setup.operations)).valid, false);
  });
});

test("verification explicitly rejects a qualification outside its closed contract", async () => {
  const setup = harness();
  const built = await buildCreatorQualificationReferences(setup.request, setup.operations);
  const qualification = structuredClone(built.qualification);
  qualification.unapproved = true;
  assert.equal((await verifyCreatorQualificationReferences({ ...setup.request, qualification }, setup.operations)).valid, false);
});

test("operational exceptions collapse to static diagnostics without paths or secrets", async () => {
  const setup = harness(runtimeFixture(), {
    loadEvidence: async () => {
      throw new Error(`${path.join(path.parse(process.cwd()).root, "private", "secret")} API_KEY=leak`);
    },
  });
  const result = await buildCreatorQualificationReferences(setup.request, setup.operations);
  assert.deepEqual(result, {
    ok: false,
    valid: false,
    diagnostics: [{
      phase: "qualification",
      severity: "error",
      code: "R16_CREATOR_QUALIFICATION_REFERENCE_INTERNAL_ERROR",
      path: "",
      message: "R16_CREATOR_QUALIFICATION_REFERENCE_INTERNAL_ERROR",
    }],
  });
  assert.doesNotMatch(JSON.stringify(result), /private|secret|API_KEY|leak/u);
});

test("qualification output is byte-identical across twenty independent builds", async () => {
  const setup = harness();
  const outputs = [];
  for (let index = 0; index < 20; index += 1) {
    const built = await buildCreatorQualificationReferences(setup.request, setup.operations);
    assert.equal(built.ok, true);
    outputs.push(`${built.qualificationRunId}:${built.canonicalQualificationJson}`);
  }
  assert.equal(new Set(outputs).size, 1);
});
