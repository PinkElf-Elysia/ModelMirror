import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  PROTOTYPE_HOST_MARKER,
  R16_PROTOTYPE_HOST_MARKER,
  createPrototypeHost,
} from "../scripts/lib/prototype-host-core.mjs";

const MODEL = "qualification-model";
const SOURCE_RUN_ID = `${"a".repeat(64)}-${"b".repeat(64)}`;
const SOLUTION_SHA256 = `sha256:${"e".repeat(64)}`;
let nextPort = 43_140;

const sha256 = (text) => `sha256:${createHash("sha256").update(text).digest("hex")}`;

function configuration(overrides = {}) {
  return { endpointHost: "api.example.test", model: MODEL, modelReady: true,
    assetsReady: true, godotReady: true, ...overrides };
}

function qualification(promptSha256, sourceRunId = SOURCE_RUN_ID, solutionSha256 = SOLUTION_SHA256) {
  const evidence = {
    runId: "f".repeat(64), attempt: 1, replayCount: 4, screenshotCount: 9,
    videoCount: 1, sampleCount: 300, medianFrameMicros: 16_666, medianFpsMilli: 60_002,
  };
  return {
    format: "matrix-oasis.prototype-creator-qualification",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    profile: "matrix-oasis.creator-solved-evidence/1",
    status: "qualified",
    promptSha256,
    model: MODEL,
    sourceRunId,
    hashes: {
      runtimePackSha256: `sha256:${"1".repeat(64)}`,
      runtimeReceiptSha256: `sha256:${"2".repeat(64)}`,
      spatialIntentSha256: `sha256:${"3".repeat(64)}`,
      environmentFactsSha256: `sha256:${"4".repeat(64)}`,
      assetBundleSha256: `sha256:${"5".repeat(64)}`,
      spatialSolutionSha256: solutionSha256,
      spatialVerificationSha256: `sha256:${"6".repeat(64)}`,
      replayPlanSha256: `sha256:${"7".repeat(64)}`,
      runtimeEvidenceSha256: `sha256:${evidence.runId}`,
    },
    toolchain: { godotVersion: "4.6.3", renderer: "forward_plus",
      evidenceProfile: "matrix-oasis.runtime-replay/1" },
    evidence,
  };
}

function qualificationResult(cacheLevel, value, reusedQualification = false) {
  return { ok: true, cacheLevel, reusedQualification,
    qualificationRunId: sha256(canonicalizeJsonValue(value)).slice(7), qualification: value };
}

function qualificationCache(value) {
  return { ok: true, cacheLevel: "qualified",
    qualificationRunId: sha256(canonicalizeJsonValue(value)).slice(7), qualification: value };
}

function fixture(overrides = {}) {
  const calls = { findCache: 0, generate: 0, acquire: 0, publish: 0, qualify: 0,
    launch: 0, recover: 0, recoverPending: 0 };
  const operations = {
    async findCache() { calls.findCache += 1; return { ok: false }; },
    async generate() { calls.generate += 1; return { ok: true, artifacts: { sceneBlueprintJson: "{}" } }; },
    async describeAssets() { return { ok: true, blueprintSha256: `sha256:${"9".repeat(64)}`,
      environmentPrompt: "A neutral connected environment.", environmentCached: true, assetsCached: true,
      briefs: [{ id: "asset-prop", kind: "prop", prompt: "A neutral prop." }] }; },
    async acquire({ onStage }) { calls.acquire += 1; onStage("normalizing"); onStage("spatializing"); return { ok: true }; },
    async publish() { calls.publish += 1; return { ok: true, runId: SOURCE_RUN_ID }; },
    async persistPending() {},
    async recoverPending() { calls.recoverPending += 1; return { runs: [] }; },
    async discardPending() {},
    async qualify({ sourceRunId, onStage }) {
      calls.qualify += 1;
      await onStage({ stage: "qualifying", subphase: "analyzing", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "solving", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "verifying", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "evidencing", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "evidencing", attempt: 1 });
      return qualificationResult("source-only",
        qualification(this.promptSha256 ?? `sha256:${"0".repeat(64)}`, sourceRunId));
    },
    async launch() { calls.launch += 1; return { ok: true }; },
    async recover() { calls.recover += 1; return { currentRunId: null, runs: [] }; },
    async stopLaunch() {},
    ...overrides,
  };
  return { calls, operations };
}

async function startHost(operations, config = configuration()) {
  const port = nextPort++;
  const origin = `http://127.0.0.1:${port}`;
  const host = createPrototypeHost({ profile: "r16", port, configuration: config, operations });
  await host.start();
  const response = await fetch(`${origin}/api/bootstrap`, { headers: { origin, connection: "close" } });
  const bootstrap = await response.json();
  return { host, origin, bootstrap, cookie: response.headers.get("set-cookie").split(";")[0] };
}

async function request(context, pathname, { method = "GET", body } = {}) {
  const response = await fetch(`${context.origin}${pathname}`, { method, headers: {
    origin: context.origin, cookie: context.cookie, connection: "close",
    ...(body === undefined ? {} : { "content-type": "application/json" }),
  }, body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: response.status, body: await response.json() };
}

async function createRun(context, prompt) {
  return request(context, "/api/runs", { method: "POST", body: { prompt } });
}

async function waitFor(context, id, status) {
  for (let index = 0; index < 200; index += 1) {
    const response = await request(context, `/api/runs/${id}`);
    if (response.body.run?.status === status) return response.body.run;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`run ${id} did not reach ${status}`);
}

test("R16 profile has a distinct marker while R10 constants remain stable", async (t) => {
  const prompt = "Build a neutral cached prototype.";
  const promptSha256 = sha256(prompt);
  const expectedQualification = qualification(promptSha256);
  const expectedQualificationRunId = qualificationResult("source-only", expectedQualification).qualificationRunId;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const candidate = fixture({
    async findCache() { return { ok: true, cacheLevel: "source-only", sourceRunId: SOURCE_RUN_ID,
      expectedSolutionSha256: null }; },
    async qualify({ sourceRunId, onStage }) {
      await onStage({ stage: "qualifying", subphase: "analyzing", attempt: 0 });
      await gate;
      for (const subphase of ["solving", "verifying", "evidencing"]) {
        await onStage({ stage: "qualifying", subphase, attempt: 0 });
      }
      return qualificationResult("source-only", qualification(promptSha256, sourceRunId));
    },
  });
  const { qualify, ...missingQualifier } = candidate.operations;
  assert.equal(typeof qualify, "function");
  assert.throws(() => createPrototypeHost({ profile: "r16", port: nextPort++, configuration: configuration(),
    operations: missingQualifier }), { code: "PROTOTYPE_HOST_INTERNAL_ERROR" });
  const context = await startHost(candidate.operations, configuration({ modelReady: false, assetsReady: false }));
  t.after(async () => { release(); await context.host.stop(); });
  assert.equal(PROTOTYPE_HOST_MARKER, "MATRIX_OASIS_R10_PROTOTYPE_HOST");
  assert.equal(R16_PROTOTYPE_HOST_MARKER, "MATRIX_OASIS_R16_PROTOTYPE_HOST");
  assert.equal(context.bootstrap.marker, R16_PROTOTYPE_HOST_MARKER);
  assert.equal(context.bootstrap.qualificationProfile, "matrix-oasis.creator-solved-evidence/1");
  const created = await createRun(context, prompt);
  assert.equal(created.status, 201);
  assert.equal(created.body.run.status, "qualifying");
  assert.equal(created.body.run.qualification.cacheLevel, "source-only");
  assert.equal(created.body.run.modelApproval, null);
  assert.equal(created.body.run.assetApproval, null);
  release();
  const ready = await waitFor(context, created.body.run.id, "ready");
  assert.equal(ready.resultRunId, expectedQualificationRunId);
  assert.equal(ready.qualification.solutionSha256, SOLUTION_SHA256);
  assert.equal(ready.qualification.evidence.sampleCount, 300);
  assert.equal(ready.qualification.reusedQualification, false);
  assert.equal(candidate.calls.generate, 0);
  assert.equal(candidate.calls.acquire, 0);
});

test("all partial cache levels continue locally and never become ready before qualification", async (t) => {
  for (const cacheLevel of ["source-only", "solved-only", "evidence-only"]) {
    const prompt = `Build cached ${cacheLevel}.`;
    const promptSha256 = sha256(prompt);
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const candidate = fixture({
      async findCache() { return { ok: true, cacheLevel, sourceRunId: SOURCE_RUN_ID,
        expectedSolutionSha256: cacheLevel === "source-only" ? null : SOLUTION_SHA256 }; },
      async qualify({ sourceRunId, onStage }) {
        await onStage({ stage: "qualifying", subphase: cacheLevel === "source-only" ? "analyzing" : "verifying", attempt: 0 });
        await gate;
        if (cacheLevel === "source-only") {
          await onStage({ stage: "qualifying", subphase: "solving", attempt: 0 });
          await onStage({ stage: "qualifying", subphase: "verifying", attempt: 0 });
        }
        if (cacheLevel !== "evidence-only") await onStage({ stage: "qualifying", subphase: "evidencing", attempt: 0 });
        return qualificationResult(cacheLevel, qualification(promptSha256, sourceRunId));
      },
    });
    const context = await startHost(candidate.operations, configuration({ modelReady: false, assetsReady: false }));
    t.after(async () => { release(); await context.host.stop(); });
    const created = await createRun(context, prompt);
    assert.equal(created.body.run.status, "qualifying");
    assert.equal((await request(context, "/api/runs/current")).body.currentRunId, null);
    release();
    const ready = await waitFor(context, created.body.run.id, "ready");
    assert.equal(ready.qualification.cacheLevel, cacheLevel);
    assert.equal(ready.cacheHit, false);
  }
});

test("a strongly revalidated qualification cache is directly ready and launch still revalidates", async (t) => {
  const prompt = "Reuse one fully qualified prototype.";
  const promptSha256 = sha256(prompt);
  const expectedQualification = qualification(promptSha256);
  const expectedQualificationRunId = qualificationCache(expectedQualification).qualificationRunId;
  let launchValid = false;
  const candidate = fixture({
    async findCache() { return qualificationCache(expectedQualification); },
    async launch({ runId }) { candidate.calls.launch += 1; assert.equal(runId, expectedQualificationRunId);
      return launchValid ? { ok: true } : { ok: false }; },
  });
  const context = await startHost(candidate.operations, configuration({ modelReady: false, assetsReady: false }));
  t.after(() => context.host.stop());
  const created = await createRun(context, prompt);
  assert.equal(created.body.run.status, "ready");
  assert.equal(created.body.run.cacheHit, true);
  assert.equal(created.body.run.qualification.cacheLevel, "qualified");
  assert.equal(created.body.run.qualification.reusedQualification, true);
  assert.equal(candidate.calls.qualify, 0);
  assert.equal((await request(context, `/api/runs/${created.body.run.id}/launch`, { method: "POST", body: {} })).status, 502);
  assert.equal((await request(context, "/api/runs/current")).body.currentRunId, expectedQualificationRunId);
  launchValid = true;
  assert.equal((await request(context, `/api/runs/${created.body.run.id}/launch`, { method: "POST", body: {} })).status, 202);
  assert.equal(candidate.calls.launch, 2);
});

test("new external acquisition enters local qualification and only qualification may replace current", async (t) => {
  const prompt = "Build a new prototype through both approvals.";
  const promptSha256 = sha256(prompt);
  const previousPromptSha256 = sha256("previous");
  const previousQualification = qualification(previousPromptSha256, `${"1".repeat(64)}-${"2".repeat(64)}`);
  const previousCache = qualificationCache(previousQualification);
  const candidate = fixture({
    async recover() { return { currentRunId: previousCache.qualificationRunId, runs: [{
      promptSha256: previousPromptSha256, model: MODEL,
      cache: previousCache,
    }] }; },
    async qualify({ sourceRunId, onStage }) {
      await onStage({ stage: "qualifying", subphase: "analyzing", attempt: 0 });
      return { ok: false, diagnostics: [{ code: "R15_RUNTIME_EVIDENCE_FAILED", path: "" }] };
    },
  });
  const context = await startHost(candidate.operations);
  t.after(() => context.host.stop());
  assert.equal(context.bootstrap.currentRunId, previousCache.qualificationRunId);
  const created = await createRun(context, prompt);
  const modelAccepted = await request(context, `/api/runs/${created.body.run.id}/approve-model`, { method: "POST",
    body: { approvalHash: created.body.run.modelApproval.approvalHash } });
  assert.equal(modelAccepted.status, 202);
  const waiting = await waitFor(context, created.body.run.id, "awaiting_asset_approval");
  const assetAccepted = await request(context, `/api/runs/${created.body.run.id}/approve-assets`, { method: "POST",
    body: { approvalHash: waiting.assetApproval.approvalHash } });
  assert.equal(assetAccepted.status, 202);
  const failed = await waitFor(context, created.body.run.id, "failed");
  assert.equal(failed.diagnostics[0].code, "R15_RUNTIME_EVIDENCE_FAILED");
  assert.equal((await request(context, "/api/runs/current")).body.currentRunId, previousCache.qualificationRunId);
  assert.deepEqual({ generate: candidate.calls.generate, acquire: candidate.calls.acquire, publish: candidate.calls.publish },
    { generate: 1, acquire: 1, publish: 1 });
  assert.equal(promptSha256, created.body.run.modelApproval.promptSha256);
});

test("restart exposes only valid qualified history and resumes at most one partial cache", async (t) => {
  const qualifiedPrompt = sha256("qualified");
  const partialPrompt = sha256("partial");
  const qualifiedCache = qualificationCache(qualification(qualifiedPrompt));
  const partialQualification = qualification(partialPrompt);
  const partialQualificationRunId = qualificationResult("source-only", partialQualification).qualificationRunId;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const candidate = fixture({
    async recover() { return { currentRunId: qualifiedCache.qualificationRunId, runs: [
      { promptSha256: qualifiedPrompt, model: MODEL,
        cache: qualifiedCache },
      { promptSha256: partialPrompt, model: MODEL,
        cache: { ok: true, cacheLevel: "source-only", sourceRunId: SOURCE_RUN_ID, expectedSolutionSha256: null } },
      { promptSha256: partialPrompt, model: MODEL,
        cache: { ok: true, cacheLevel: "solved-only", sourceRunId: SOURCE_RUN_ID, expectedSolutionSha256: SOLUTION_SHA256 } },
    ] }; },
    async qualify({ sourceRunId, onStage }) {
      await onStage({ stage: "qualifying", subphase: "analyzing", attempt: 0 });
      await gate;
      for (const subphase of ["solving", "verifying", "evidencing"]) await onStage({ stage: "qualifying", subphase, attempt: 0 });
      return qualificationResult("source-only", qualification(partialPrompt, sourceRunId));
    },
  });
  const context = await startHost(candidate.operations, configuration({ modelReady: false, assetsReady: false }));
  t.after(async () => { release(); await context.host.stop(); });
  assert.equal(context.bootstrap.currentRunId, qualifiedCache.qualificationRunId);
  assert.equal(context.bootstrap.runs.filter(({ status }) => status === "ready").length, 1);
  assert.equal(context.bootstrap.runs.filter(({ status }) => status === "qualifying").length, 1);
  const blockedLaunch = await request(context, "/api/runs/r10-run-1/launch", { method: "POST", body: {} });
  assert.equal(blockedLaunch.status, 409);
  assert.equal((await createRun(context, "Another prompt.")).status, 409);
  release();
  assert.equal((await waitFor(context, "r10-run-2", "ready")).resultRunId, partialQualificationRunId);
});

test("invalid qualification stages and mismatched qualification identities fail closed", async (t) => {
  const prompt = "Reject malformed qualification callbacks.";
  const promptSha256 = sha256(prompt);
  const candidate = fixture({
    async findCache() { return { ok: true, cacheLevel: "source-only", sourceRunId: SOURCE_RUN_ID,
      expectedSolutionSha256: null }; },
    async qualify({ onStage }) {
      await onStage({ stage: "qualifying", subphase: "evidencing", attempt: 2 });
      return qualificationResult("source-only", qualification(promptSha256));
    },
  });
  const context = await startHost(candidate.operations);
  t.after(() => context.host.stop());
  const created = await createRun(context, prompt);
  const failed = await waitFor(context, created.body.run.id, "failed");
  assert.equal(failed.diagnostics[0].code, "PROTOTYPE_HOST_QUALIFICATION_FAILED");
  assert.equal((await request(context, "/api/runs/current")).body.currentRunId, null);
});

test("repair attempts cannot jump and qualification identity cannot change source", async (t) => {
  const cases = [
    async ({ onStage }) => {
      await onStage({ stage: "qualifying", subphase: "analyzing", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "solving", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "verifying", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "evidencing", attempt: 0 });
      await onStage({ stage: "qualifying", subphase: "evidencing", attempt: 2 });
      return { ok: false };
    },
    async ({ onStage }) => {
      for (const subphase of ["analyzing", "solving", "verifying", "evidencing"]) {
        await onStage({ stage: "qualifying", subphase, attempt: 0 });
      }
      return qualificationResult("source-only",
        qualification(sha256("Reject source drift."), `${"1".repeat(64)}-${"2".repeat(64)}`));
    },
  ];
  for (const qualify of cases) {
    const prompt = "Reject source drift.";
    const candidate = fixture({
      async findCache() { return { ok: true, cacheLevel: "source-only", sourceRunId: SOURCE_RUN_ID,
        expectedSolutionSha256: null }; },
      qualify,
    });
    const context = await startHost(candidate.operations);
    t.after(() => context.host.stop());
    const created = await createRun(context, prompt);
    const failed = await waitFor(context, created.body.run.id, "failed");
    assert.equal(failed.diagnostics[0].code, "PROTOTYPE_HOST_QUALIFICATION_FAILED");
    assert.equal((await request(context, "/api/runs/current")).body.currentRunId, null);
  }
});
